-- ComMach Phase 2: calibration (built as a generic work model that Phase 3 reuses for preventive maintenance).
--   templates / template_items   what is done: calibration points with tolerances (later: PM checklist items)
--   schedules                    when and by whom: interval, floating or fixed basis, asymmetric tolerance window,
--                                internal assignees or an external vendor with an internal responsible person
--   tasks                        each due occurrence: accept, send to vendor, results, signed completion, review
--   task_results / task_standards  readings as found / as left, and the reference standards used
--   impact_assessments           what an out-of-tolerance result means for past use, approved by QA

ALTER TABLE tenants ADD COLUMN work_settings jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------- numbering (CAL-2026-0001, IA-2026-001)
CREATE TABLE counters (
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  key       text NOT NULL,
  value     integer NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, key)
);

CREATE OR REPLACE FUNCTION cm_next_number(p_tenant uuid, p_prefix text, p_width int) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE k text := p_prefix || '-' || to_char(now() AT TIME ZONE 'UTC', 'YYYY'); v int;
BEGIN
  INSERT INTO counters (tenant_id, key, value) VALUES (p_tenant, k, 1)
  ON CONFLICT (tenant_id, key) DO UPDATE SET value = counters.value + 1 RETURNING value INTO v;
  RETURN k || '-' || lpad(v::text, p_width, '0');
END $$;

-- ---------------------------------------------------------------- templates
CREATE TABLE templates (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  kind             text NOT NULL CHECK (kind IN ('calibration', 'pm')),
  code             text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$'),
  version          integer NOT NULL DEFAULT 1 CHECK (version > 0),
  title            text NOT NULL CHECK (length(trim(title)) > 0),
  title_ar         text,
  class_id         uuid REFERENCES asset_classes(id),   -- the class it is written for (optional)
  method_ref       text,                                -- SOP / method number
  description      text,
  standards_note   text,                                -- reference standards required
  environment_note text,                                -- conditions required, e.g. 20 ± 2 °C
  status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded', 'retired')),
  based_on         uuid REFERENCES templates(id),
  approved_signature_id bigint REFERENCES signatures(id),
  approved_at      timestamptz,
  approved_by_name text,
  source           text NOT NULL DEFAULT 'normal' CHECK (source IN ('normal', 'demo')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  created_by       uuid,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  updated_by       uuid,
  UNIQUE (tenant_id, code, version),
  CHECK (status = 'draft' OR approved_signature_id IS NOT NULL OR source = 'demo')
);

CREATE TABLE template_items (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  template_id uuid NOT NULL REFERENCES templates(id),
  seq         integer NOT NULL,
  item_type   text NOT NULL DEFAULT 'point' CHECK (item_type IN ('point', 'check', 'reading', 'text', 'photo')),
  parameter   text NOT NULL CHECK (length(trim(parameter)) > 0),
  nominal     numeric,
  unit        text,
  tol_mode    text CHECK (tol_mode IN ('abs', 'pct', 'limits')),
  tol_minus   numeric CHECK (tol_minus IS NULL OR tol_minus >= 0),
  tol_plus    numeric CHECK (tol_plus IS NULL OR tol_plus >= 0),
  low_limit   numeric,
  high_limit  numeric,
  required    boolean NOT NULL DEFAULT true,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid,
  CHECK (item_type <> 'point' OR (nominal IS NOT NULL AND tol_mode IS NOT NULL)),
  CHECK (low_limit IS NULL OR high_limit IS NULL OR low_limit <= high_limit)
);
CREATE INDEX template_items_template_idx ON template_items (template_id, seq);

-- An approved template is frozen: change it by making a new version.
CREATE OR REPLACE FUNCTION cm_template_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    IF NEW.status IS DISTINCT FROM OLD.status AND OLD.status = 'approved' AND NEW.status IN ('superseded', 'retired')
       AND (to_jsonb(NEW) - 'status' - 'updated_at' - 'updated_by') = (to_jsonb(OLD) - 'status' - 'updated_at' - 'updated_by') THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'An approved template cannot be changed; make a new version' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'approved' AND NEW.approved_signature_id IS NULL AND NEW.source <> 'demo' THEN
    RAISE EXCEPTION 'A template is approved only with an electronic signature' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER templates_guard BEFORE UPDATE ON templates FOR EACH ROW EXECUTE FUNCTION cm_template_guard();

CREATE OR REPLACE FUNCTION cm_template_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  SELECT status INTO st FROM templates WHERE id = coalesce(NEW.template_id, OLD.template_id);
  IF st <> 'draft' THEN
    RAISE EXCEPTION 'The items of an approved template cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER template_items_guard BEFORE INSERT OR UPDATE OR DELETE ON template_items
  FOR EACH ROW EXECUTE FUNCTION cm_template_items_guard();

-- ---------------------------------------------------------------- schedules
CREATE TABLE schedules (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id),
  site_id             uuid NOT NULL REFERENCES sites(id),
  asset_id            uuid NOT NULL REFERENCES assets(id),
  kind                text NOT NULL CHECK (kind IN ('calibration', 'pm')),
  template_id         uuid REFERENCES templates(id),
  title               text,
  interval_value      integer NOT NULL CHECK (interval_value BETWEEN 1 AND 3650),
  interval_unit       text NOT NULL CHECK (interval_unit IN ('day', 'week', 'month', 'year')),
  basis               text NOT NULL DEFAULT 'floating' CHECK (basis IN ('floating', 'fixed')),
  -- tolerance window around the due date; NULL value = take the company rule for this interval
  tol_before_value    numeric CHECK (tol_before_value IS NULL OR tol_before_value >= 0),
  tol_before_unit     text CHECK (tol_before_unit IN ('day', 'week', 'month', 'percent', 'any')),
  tol_after_value     numeric CHECK (tol_after_value IS NULL OR tol_after_value >= 0),
  tol_after_unit      text CHECK (tol_after_unit IN ('day', 'week', 'month', 'percent')),
  performer           text NOT NULL DEFAULT 'internal' CHECK (performer IN ('internal', 'external')),
  vendor_id           uuid REFERENCES vendors(id),
  responsible_user_id uuid REFERENCES users(id),
  assignee_user_ids   uuid[] NOT NULL DEFAULT '{}',
  assignee_roles      text[] NOT NULL DEFAULT '{}',
  certificate_required boolean NOT NULL DEFAULT false,
  last_done           date,
  next_due            date,
  active              boolean NOT NULL DEFAULT true,
  notes               text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          uuid,
  CHECK (performer = 'internal' OR vendor_id IS NOT NULL)
);
CREATE INDEX schedules_asset_idx ON schedules (asset_id);
CREATE INDEX schedules_due_idx ON schedules (site_id, kind, next_due) WHERE active;

-- ---------------------------------------------------------------- tasks
CREATE TABLE tasks (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id),
  site_id             uuid NOT NULL REFERENCES sites(id),
  asset_id            uuid NOT NULL REFERENCES assets(id),
  schedule_id         uuid REFERENCES schedules(id),
  kind                text NOT NULL CHECK (kind IN ('calibration', 'pm')),
  number              text NOT NULL,
  template_id         uuid REFERENCES templates(id),
  due_date            date NOT NULL,
  window_start        date,                 -- NULL = may be done early at any time
  window_end          date NOT NULL,        -- after this date the task is overdue
  status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'in_progress', 'at_vendor', 'completed', 'cancelled')),
  performer           text NOT NULL DEFAULT 'internal' CHECK (performer IN ('internal', 'external')),
  vendor_id           uuid REFERENCES vendors(id),
  responsible_user_id uuid REFERENCES users(id),
  assignee_user_ids   uuid[] NOT NULL DEFAULT '{}',
  assignee_roles      text[] NOT NULL DEFAULT '{}',
  assigned_user_id    uuid REFERENCES users(id),
  accepted_at         timestamptz,
  accepted_by_name    text,
  sent_at             timestamptz,
  performed_date      date,
  performed_by_name   text,
  certificate_no      text,
  certificate_file_id uuid REFERENCES files(id),
  certificate_required boolean NOT NULL DEFAULT false,
  env_temperature     text,
  env_humidity        text,
  verdict             text CHECK (verdict IN ('pass', 'adjusted', 'fail')),
  remarks             text,
  ai_suggestion_id    uuid REFERENCES ai_suggestions(id),
  completed_at        timestamptz,
  completed_by        uuid,
  completed_by_name   text,
  signature_id        bigint REFERENCES signatures(id),
  reviewed_at         timestamptz,
  reviewed_by_name    text,
  review_signature_id bigint REFERENCES signatures(id),
  cancel_reason       text,
  source              text NOT NULL DEFAULT 'normal' CHECK (source IN ('normal', 'demo')),
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          uuid,
  UNIQUE (tenant_id, number),
  CHECK (window_start IS NULL OR window_start <= due_date),
  CHECK (window_end >= due_date),
  CHECK (status <> 'completed' OR (verdict IS NOT NULL AND performed_date IS NOT NULL
                                   AND (signature_id IS NOT NULL OR source = 'demo')))
);
CREATE INDEX tasks_open_idx ON tasks (site_id, kind, due_date) WHERE status NOT IN ('completed', 'cancelled');
CREATE INDEX tasks_asset_idx ON tasks (asset_id, kind, due_date DESC);

-- A completed or cancelled task is a closed record: only its one review may be added.
CREATE OR REPLACE FUNCTION cm_task_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Tasks cannot be removed; cancel them with a reason' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status IN ('completed', 'cancelled') THEN
    IF OLD.reviewed_at IS NULL AND NEW.reviewed_at IS NOT NULL AND NEW.review_signature_id IS NOT NULL
       AND (to_jsonb(NEW) - 'reviewed_at' - 'reviewed_by_name' - 'review_signature_id' - 'updated_at' - 'updated_by')
         = (to_jsonb(OLD) - 'reviewed_at' - 'reviewed_by_name' - 'review_signature_id' - 'updated_at' - 'updated_by') THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A closed task cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'completed' AND NEW.signature_id IS NULL AND NEW.source <> 'demo' THEN
    RAISE EXCEPTION 'A task is completed only with an electronic signature' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tasks_guard BEFORE UPDATE OR DELETE ON tasks FOR EACH ROW EXECUTE FUNCTION cm_task_guard();

CREATE TABLE task_results (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  task_id     uuid NOT NULL REFERENCES tasks(id),
  item_id     uuid REFERENCES template_items(id),
  seq         integer NOT NULL,
  item_type   text NOT NULL DEFAULT 'point' CHECK (item_type IN ('point', 'check', 'reading', 'text', 'photo')),
  parameter   text NOT NULL,
  nominal     numeric,
  unit        text,
  low_limit   numeric,
  high_limit  numeric,
  as_found    numeric,
  as_left     numeric,
  uncertainty numeric,
  value_text  text,
  ok_found    boolean,
  ok_left     boolean,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid
);
CREATE INDEX task_results_task_idx ON task_results (task_id, seq);

CREATE TABLE task_standards (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  task_id           uuid NOT NULL REFERENCES tasks(id),
  standard_asset_id uuid REFERENCES assets(id),
  description       text NOT NULL,            -- code and name, or the lab's standard as printed on a certificate
  cert_ref          text,
  valid_until       date,                     -- the standard's own calibration due date when it was used
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid
);
CREATE INDEX task_standards_task_idx ON task_standards (task_id);

CREATE OR REPLACE FUNCTION cm_task_child_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  SELECT status INTO st FROM tasks WHERE id = coalesce(NEW.task_id, OLD.task_id);
  IF st IN ('completed', 'cancelled') AND coalesce(cp_setting('app.status_change'), '') <> 'demo' THEN
    RAISE EXCEPTION 'The results of a closed task cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER task_results_guard BEFORE INSERT OR UPDATE OR DELETE ON task_results FOR EACH ROW EXECUTE FUNCTION cm_task_child_guard();
CREATE TRIGGER task_standards_guard BEFORE INSERT OR UPDATE OR DELETE ON task_standards FOR EACH ROW EXECUTE FUNCTION cm_task_child_guard();

-- ---------------------------------------------------------------- impact assessments
CREATE TABLE impact_assessments (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid NOT NULL REFERENCES tenants(id),
  site_id               uuid NOT NULL REFERENCES sites(id),
  asset_id              uuid NOT NULL REFERENCES assets(id),
  task_id               uuid NOT NULL REFERENCES tasks(id),
  number                text NOT NULL,
  period_from           date,                 -- last calibration found in tolerance
  period_to             date NOT NULL,        -- the out-of-tolerance calibration
  status                text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
  summary               text,
  scope                 text,                 -- rooms, lines, equipment and tests that relied on the instrument
  products              text,                 -- products / batches / tests affected
  risk                  text CHECK (risk IN ('low', 'medium', 'high')),
  actions               text,
  conclusion            text,
  ai_suggestion_id      uuid REFERENCES ai_suggestions(id),
  approved_signature_id bigint REFERENCES signatures(id),
  approved_at           timestamptz,
  approved_by_name      text,
  source                text NOT NULL DEFAULT 'normal' CHECK (source IN ('normal', 'demo')),
  created_at            timestamptz NOT NULL DEFAULT now(),
  created_by            uuid,
  updated_at            timestamptz NOT NULL DEFAULT now(),
  updated_by            uuid,
  UNIQUE (tenant_id, number),
  CHECK (status = 'draft' OR approved_signature_id IS NOT NULL OR source = 'demo')
);

CREATE OR REPLACE FUNCTION cm_impact_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR OLD.status = 'approved' THEN
    RAISE EXCEPTION 'An approved impact assessment cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER impact_guard BEFORE UPDATE OR DELETE ON impact_assessments FOR EACH ROW EXECUTE FUNCTION cm_impact_guard();

-- ---------------------------------------------------------------- stamping, audit, RLS, privileges
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['templates', 'template_items', 'schedules', 'tasks', 'task_results', 'task_standards',
                           'impact_assessments'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION cp_stamp()', t || '_stamp', t);
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cp_audit_row()', t || '_audit', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['counters', 'templates', 'template_items', 'schedules', 'tasks', 'task_results', 'task_standards',
                           'impact_assessments'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (cp_tenant_only(tenant_id)) WITH CHECK (cp_tenant_only(tenant_id))', t || '_tenant', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON counters, templates, schedules, tasks, impact_assessments TO commach_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON template_items, task_results, task_standards TO commach_app;

-- Integration: calibration and maintenance events for Complanning (Phase 6 adds planned windows).
CREATE OR REPLACE FUNCTION cm_task_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.status = 'completed' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'completed') THEN
    INSERT INTO integration_events (tenant_id, event, entity, record_id, payload)
    VALUES (NEW.tenant_id, NEW.kind || '.completed', 'task', NEW.id, jsonb_build_object(
      'number', NEW.number, 'asset_id', NEW.asset_id, 'performed_date', NEW.performed_date, 'verdict', NEW.verdict));
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tasks_event AFTER INSERT OR UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION cm_task_event();

-- Demo records (no signatures) can only be written by the demo loader, which sets app.status_change = 'demo'.
CREATE OR REPLACE FUNCTION cm_demo_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.source = 'demo' AND coalesce(cp_setting('app.status_change'), '') <> 'demo' THEN
    RAISE EXCEPTION 'Only the demo loader can write demo records' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER templates_demo BEFORE INSERT ON templates FOR EACH ROW EXECUTE FUNCTION cm_demo_guard();
CREATE TRIGGER tasks_demo BEFORE INSERT ON tasks FOR EACH ROW EXECUTE FUNCTION cm_demo_guard();
CREATE TRIGGER impact_demo BEFORE INSERT ON impact_assessments FOR EACH ROW EXECUTE FUNCTION cm_demo_guard();
