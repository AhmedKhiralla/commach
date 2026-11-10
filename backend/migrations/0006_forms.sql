-- ComMach Phase 5: forms and records.
--   form_templates  controlled documents that print ComMach records: a block layout (rendered to PDF) or the
--                   company's own Word template with merge fields (rendered to DOCX); approved and versioned
--   form_issues     every printed copy: controlled-copy number, template version, record, SHA-256 of the file

CREATE TABLE form_templates (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid NOT NULL REFERENCES tenants(id),
  code                  text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$'),
  version               integer NOT NULL DEFAULT 1 CHECK (version > 0),
  title                 text NOT NULL CHECK (length(trim(title)) > 0),
  source                text NOT NULL CHECK (source IN ('calibration_plan', 'pm_plan', 'calibration_record', 'pm_record',
                                                        'history_card', 'status_label')),
  engine                text NOT NULL CHECK (engine IN ('blocks', 'docx')),
  layout                jsonb NOT NULL DEFAULT '{}'::jsonb,   -- block designer layout
  file_id               uuid REFERENCES files(id),            -- Word template
  format_no             text,                                 -- e.g. QA027-00/F01
  revision              text,
  is_default            boolean NOT NULL DEFAULT false,       -- used by the Print buttons for its source
  status                text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded', 'retired')),
  based_on              uuid REFERENCES form_templates(id),
  approved_signature_id bigint REFERENCES signatures(id),
  approved_at           timestamptz,
  approved_by_name      text,
  source_kind           text NOT NULL DEFAULT 'normal' CHECK (source_kind IN ('normal', 'demo')),
  created_at            timestamptz NOT NULL DEFAULT now(),
  created_by            uuid,
  updated_at            timestamptz NOT NULL DEFAULT now(),
  updated_by            uuid,
  UNIQUE (tenant_id, code, version),
  CHECK (engine <> 'docx' OR file_id IS NOT NULL),
  CHECK (status = 'draft' OR approved_signature_id IS NOT NULL OR source_kind = 'demo')
);

-- An approved form is frozen except for the default flag and its retirement; changes need a new version.
CREATE OR REPLACE FUNCTION cm_form_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Forms cannot be removed; retire them' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status <> 'draft' THEN
    IF (to_jsonb(NEW) - 'status' - 'is_default' - 'updated_at' - 'updated_by')
       = (to_jsonb(OLD) - 'status' - 'is_default' - 'updated_at' - 'updated_by')
       AND (NEW.status = OLD.status OR (OLD.status = 'approved' AND NEW.status IN ('superseded', 'retired'))) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'An approved form cannot be changed; make a new version' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'approved' AND NEW.approved_signature_id IS NULL AND NEW.source_kind <> 'demo' THEN
    RAISE EXCEPTION 'A form is approved only with an electronic signature' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER form_templates_guard BEFORE UPDATE OR DELETE ON form_templates FOR EACH ROW EXECUTE FUNCTION cm_form_guard();

CREATE OR REPLACE FUNCTION cm_form_demo_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.source_kind = 'demo' AND coalesce(cp_setting('app.status_change'), '') <> 'demo' THEN
    RAISE EXCEPTION 'Only the demo loader can write demo records' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER form_templates_demo BEFORE INSERT ON form_templates FOR EACH ROW EXECUTE FUNCTION cm_form_demo_guard();

CREATE TABLE form_issues (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  number        text NOT NULL,              -- controlled copy number, e.g. CC-2026-00012
  template_id   uuid NOT NULL REFERENCES form_templates(id),
  template_code text NOT NULL,
  template_version integer NOT NULL,
  template_status  text NOT NULL,           -- a draft prints watermarked "not for GMP use"
  source        text NOT NULL,
  record_ref    text NOT NULL,              -- e.g. task CAL-2026-0012, plan OCT 2027, asset TP-01
  record_id     uuid,
  format        text NOT NULL CHECK (format IN ('pdf', 'docx')),
  sha256        text NOT NULL,
  issued_by     uuid,
  issued_by_name text NOT NULL,
  issued_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, number)
);
CREATE INDEX form_issues_record_idx ON form_issues (record_id);

CREATE OR REPLACE FUNCTION cm_issue_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Issued copies are a permanent record' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER form_issues_immutable BEFORE UPDATE OR DELETE ON form_issues FOR EACH ROW EXECUTE FUNCTION cm_issue_immutable();

CREATE TRIGGER form_templates_stamp BEFORE INSERT OR UPDATE ON form_templates FOR EACH ROW EXECUTE FUNCTION cp_stamp();
CREATE TRIGGER form_templates_audit AFTER INSERT OR UPDATE OR DELETE ON form_templates FOR EACH ROW EXECUTE FUNCTION cp_audit_row();
CREATE TRIGGER form_issues_audit AFTER INSERT ON form_issues FOR EACH ROW EXECUTE FUNCTION cp_audit_row();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['form_templates', 'form_issues'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (cp_tenant_only(tenant_id)) WITH CHECK (cp_tenant_only(tenant_id))', t || '_tenant', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON form_templates TO commach_app;
GRANT SELECT, INSERT ON form_issues TO commach_app;

-- Word templates and logos are controlled files too.
ALTER TABLE files DROP CONSTRAINT files_purpose_check;
ALTER TABLE files ADD CONSTRAINT files_purpose_check
  CHECK (purpose IN ('photo', 'nameplate', 'manual', 'certificate', 'import', 'attachment', 'export', 'form', 'logo'));
