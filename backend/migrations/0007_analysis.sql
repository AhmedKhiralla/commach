-- ComMach Phase 6: analysis, assistant, Complanning connection and run-hours triggers.
--   meter_readings       run-hours (or cycles) per asset, entered by people or sent by Complanning; append-only
--   schedules.hours_*    a PM or calibration falls due after N run-hours or at its date, whichever comes first
--   production_windows   the released production plan per machine, sent by Complanning (to find PM gaps)
--   assistant_questions  every "Ask ComMach" question and answer, with the look-ups it made
--   integration events   calibration.planned / maintenance.planned / … for Complanning downtime

-- ---------------------------------------------------------------- run-hours
ALTER TABLE assets ADD COLUMN meter_unit text CHECK (meter_unit IS NULL OR meter_unit IN ('hours', 'cycles'));

CREATE TABLE meter_readings (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  asset_id       uuid NOT NULL REFERENCES assets(id),
  reading        numeric NOT NULL CHECK (reading >= 0),
  read_at        timestamptz NOT NULL,
  source         text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'complanning', 'demo')),
  meter_reset    boolean NOT NULL DEFAULT false,   -- a new or replaced meter starts again from its own reading
  note           text,
  entered_by     uuid,
  entered_by_name text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX meter_readings_asset_idx ON meter_readings (asset_id, read_at DESC);

-- Readings only go up, unless the meter was replaced (recorded with a note).
CREATE OR REPLACE FUNCTION cm_meter_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prev numeric;
BEGIN
  IF NEW.meter_reset THEN
    IF coalesce(trim(NEW.note), '') = '' THEN
      RAISE EXCEPTION 'Say why the meter starts again (for example: meter replaced)' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  SELECT reading INTO prev FROM meter_readings
   WHERE asset_id = NEW.asset_id AND read_at <= NEW.read_at ORDER BY read_at DESC, created_at DESC LIMIT 1;
  IF prev IS NOT NULL AND NEW.reading < prev THEN
    RAISE EXCEPTION 'The reading % is lower than the previous reading %', NEW.reading, prev USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER meter_readings_check BEFORE INSERT ON meter_readings FOR EACH ROW EXECUTE FUNCTION cm_meter_check();
CREATE TRIGGER meter_readings_append_only BEFORE UPDATE OR DELETE ON meter_readings FOR EACH ROW EXECUTE FUNCTION cp_append_only();

ALTER TABLE schedules
  ADD COLUMN hours_interval numeric CHECK (hours_interval IS NULL OR (hours_interval > 0 AND hours_interval <= 1000000)),
  ADD COLUMN hours_at_last  numeric CHECK (hours_at_last IS NULL OR hours_at_last >= 0);
ALTER TABLE tasks ADD COLUMN due_by_hours boolean NOT NULL DEFAULT false;

-- ---------------------------------------------------------------- production plan from Complanning
CREATE TABLE production_windows (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  asset_id     uuid NOT NULL REFERENCES assets(id),
  starts_at    timestamptz NOT NULL,
  ends_at      timestamptz NOT NULL,
  label        text,                -- product / campaign
  ref          text,                -- Complanning's own reference
  source       text NOT NULL DEFAULT 'complanning' CHECK (source IN ('complanning', 'demo')),
  received_at  timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at)
);
CREATE INDEX production_windows_asset_idx ON production_windows (asset_id, starts_at);

-- ---------------------------------------------------------------- assistant log
CREATE TABLE assistant_questions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  user_id     uuid NOT NULL,
  user_name   text NOT NULL,
  question    text NOT NULL,
  answer      text,
  lookups     jsonb NOT NULL DEFAULT '[]'::jsonb,
  model       text,
  error       text,
  asked_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assistant_questions_user_idx ON assistant_questions (user_id, asked_at DESC);
CREATE TRIGGER assistant_questions_append_only BEFORE UPDATE OR DELETE ON assistant_questions FOR EACH ROW EXECUTE FUNCTION cp_append_only();

-- ---------------------------------------------------------------- integration events for Complanning
CREATE OR REPLACE FUNCTION cm_task_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE ev text; base text; a record; mins integer;
BEGIN
  base := CASE NEW.kind WHEN 'pm' THEN 'maintenance' ELSE 'calibration' END;
  IF NEW.status = 'cancelled' THEN
    IF TG_OP = 'UPDATE' AND OLD.status = 'cancelled' THEN RETURN NEW; END IF;
    ev := base || '.cancelled';
  ELSIF NEW.status = 'completed' THEN
    IF TG_OP = 'UPDATE' AND OLD.status = 'completed' THEN RETURN NEW; END IF;
    ev := NEW.kind || '.completed';        -- calibration.completed / pm.completed, as since Phase 2
  ELSIF TG_OP = 'INSERT' OR NEW.due_date IS DISTINCT FROM OLD.due_date OR NEW.window_start IS DISTINCT FROM OLD.window_start
        OR NEW.window_end IS DISTINCT FROM OLD.window_end OR NEW.status IS DISTINCT FROM OLD.status THEN
    ev := base || '.planned';
  ELSE
    RETURN NEW;
  END IF;
  SELECT code, external_ref INTO a FROM assets WHERE id = NEW.asset_id;
  SELECT est_minutes INTO mins FROM templates WHERE id = NEW.template_id;
  INSERT INTO integration_events (tenant_id, event, entity, record_id, payload)
  VALUES (NEW.tenant_id, ev, 'task', NEW.id, jsonb_build_object(
    'number', NEW.number, 'kind', NEW.kind, 'asset_id', NEW.asset_id, 'asset_code', a.code, 'external_ref', a.external_ref,
    'site_id', NEW.site_id, 'status', NEW.status, 'due_date', NEW.due_date, 'window_start', NEW.window_start,
    'window_end', NEW.window_end, 'duration_minutes', mins, 'performer', NEW.performer, 'performed_date', NEW.performed_date,
    'verdict', NEW.verdict, 'due_by_hours', NEW.due_by_hours));
  RETURN NEW;
END $$;
-- (replaces the Phase 3 function; the tasks_event trigger already calls it)

-- An asset that stops being available is an unplanned downtime for Complanning.
CREATE OR REPLACE FUNCTION cm_asset_down_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.status IN ('out_of_service', 'quarantined') AND OLD.status IN ('in_service', 'restricted', 'standby') THEN
    INSERT INTO integration_events (tenant_id, event, entity, record_id, payload)
    VALUES (NEW.tenant_id, 'asset.out_of_service', 'asset', NEW.id, jsonb_build_object(
      'code', NEW.code, 'external_ref', NEW.external_ref, 'site_id', NEW.site_id, 'status', NEW.status,
      'previous_status', OLD.status, 'since', clock_timestamp()));
  ELSIF OLD.status IN ('out_of_service', 'quarantined') AND NEW.status IN ('in_service', 'restricted') THEN
    INSERT INTO integration_events (tenant_id, event, entity, record_id, payload)
    VALUES (NEW.tenant_id, 'asset.back_in_service', 'asset', NEW.id, jsonb_build_object(
      'code', NEW.code, 'external_ref', NEW.external_ref, 'site_id', NEW.site_id, 'status', NEW.status,
      'previous_status', OLD.status, 'since', clock_timestamp()));
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER assets_down_event AFTER UPDATE OF status ON assets FOR EACH ROW EXECUTE FUNCTION cm_asset_down_event();

-- ---------------------------------------------------------------- stamping, audit, row-level security, privileges
CREATE TRIGGER meter_readings_audit AFTER INSERT ON meter_readings FOR EACH ROW EXECUTE FUNCTION cp_audit_row();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['meter_readings', 'production_windows', 'assistant_questions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (cp_tenant_only(tenant_id)) WITH CHECK (cp_tenant_only(tenant_id))', t || '_tenant', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT ON meter_readings, assistant_questions TO commach_app;
GRANT SELECT, INSERT, DELETE ON production_windows TO commach_app;
