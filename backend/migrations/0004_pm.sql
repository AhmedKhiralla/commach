-- ComMach Phase 3: preventive maintenance on the Phase 2 work model, and annual plan approval.
--   templates        + spare parts, suggested interval, estimated time, the manual it came from (AI)
--   template_items   + page reference in the manual
--   task_results     + photo file for photo items
--   tasks            + man-hours
--   task_parts       spare parts used on a task
--   plans            the annual calibration / PM plan of a site, reviewed by a supervisor and approved by QA

ALTER TABLE templates
  ADD COLUMN spare_parts              jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN suggested_interval_value integer CHECK (suggested_interval_value IS NULL OR suggested_interval_value BETWEEN 1 AND 3650),
  ADD COLUMN suggested_interval_unit  text CHECK (suggested_interval_unit IN ('day', 'week', 'month', 'year')),
  ADD COLUMN est_minutes              integer CHECK (est_minutes IS NULL OR est_minutes BETWEEN 1 AND 100000),
  ADD COLUMN source_file_id           uuid REFERENCES files(id),
  ADD COLUMN ai_suggestion_id         uuid REFERENCES ai_suggestions(id);

ALTER TABLE template_items ADD COLUMN page_ref text;

ALTER TABLE task_results ADD COLUMN file_id uuid REFERENCES files(id), ADD COLUMN required boolean NOT NULL DEFAULT true;
ALTER TABLE task_results ADD CONSTRAINT task_results_check_value
  CHECK (item_type <> 'check' OR value_text IS NULL OR value_text IN ('ok', 'not_ok', 'na'));

ALTER TABLE tasks ADD COLUMN man_hours numeric CHECK (man_hours IS NULL OR (man_hours >= 0 AND man_hours <= 10000));

-- ---------------------------------------------------------------- spare parts used
CREATE TABLE task_parts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  task_id     uuid NOT NULL REFERENCES tasks(id),
  name        text NOT NULL CHECK (length(trim(name)) > 0),
  part_no     text,
  qty         numeric NOT NULL DEFAULT 1 CHECK (qty > 0),
  unit        text,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid
);
CREATE INDEX task_parts_task_idx ON task_parts (task_id);
CREATE TRIGGER task_parts_guard BEFORE INSERT OR UPDATE OR DELETE ON task_parts FOR EACH ROW EXECUTE FUNCTION cm_task_child_guard();

-- ---------------------------------------------------------------- annual plans
CREATE TABLE plans (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid NOT NULL REFERENCES tenants(id),
  site_id                uuid NOT NULL REFERENCES sites(id),
  kind                   text NOT NULL CHECK (kind IN ('calibration', 'pm')),
  year                   integer NOT NULL CHECK (year BETWEEN 2000 AND 2100),
  status                 text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'approved')),
  task_count             integer,
  content_hash           text,                -- SHA-256 of the task list that was signed
  reviewed_signature_id  bigint REFERENCES signatures(id),
  reviewed_at            timestamptz,
  reviewed_by_name       text,
  approved_signature_id  bigint REFERENCES signatures(id),
  approved_at            timestamptz,
  approved_by_name       text,
  notes                  text,
  source                 text NOT NULL DEFAULT 'normal' CHECK (source IN ('normal', 'demo')),
  created_at             timestamptz NOT NULL DEFAULT now(),
  created_by             uuid,
  updated_at             timestamptz NOT NULL DEFAULT now(),
  updated_by             uuid,
  UNIQUE (tenant_id, site_id, kind, year),
  CHECK (status = 'draft' OR reviewed_signature_id IS NOT NULL OR source = 'demo'),
  CHECK (status <> 'approved' OR approved_signature_id IS NOT NULL OR source = 'demo')
);

-- Sequence: draft -> reviewed (supervisor signature) -> approved (QA signature). An approved plan is final;
-- a reviewed plan may go back to draft (the plan changed) only by clearing the review.
CREATE OR REPLACE FUNCTION cm_plan_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR OLD.status = 'approved' THEN
    RAISE EXCEPTION 'An approved plan cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'reviewed' AND NEW.reviewed_signature_id IS NULL AND NEW.source <> 'demo' THEN
    RAISE EXCEPTION 'A plan is reviewed only with an electronic signature' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'approved' AND (OLD.status <> 'reviewed' OR NEW.approved_signature_id IS NULL) AND NEW.source <> 'demo' THEN
    RAISE EXCEPTION 'A plan is approved by QA with an electronic signature after its review' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER plans_guard BEFORE UPDATE OR DELETE ON plans FOR EACH ROW EXECUTE FUNCTION cm_plan_guard();
CREATE TRIGGER plans_demo BEFORE INSERT ON plans FOR EACH ROW EXECUTE FUNCTION cm_demo_guard();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['task_parts', 'plans'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION cp_stamp()', t || '_stamp', t);
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cp_audit_row()', t || '_audit', t);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (cp_tenant_only(tenant_id)) WITH CHECK (cp_tenant_only(tenant_id))', t || '_tenant', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON plans TO commach_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON task_parts TO commach_app;
