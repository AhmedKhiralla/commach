-- ComMach Phase 1: the asset register.
--   Hierarchy  Group -> Site -> Building -> Area -> Room (every level optional), plus production lines
--   Lists      departments, asset classes, vendors and their contacts
--   Assets     machines, instruments, gauges, sensors, tools, reference standards, utilities, facility and safety
--              items, with parent/child assets and a lifecycle status that changes only with an e-signature
--   Records    controlled files, electronic signatures, AI suggestions (draft until a person accepts them),
--              integration events for other Comp11 apps (Complanning)

-- AI settings per company (provider, daily limit)
ALTER TABLE tenants ADD COLUMN engine jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------- hierarchy
CREATE TABLE site_groups (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  code        text NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,19}$'),
  name        text NOT NULL CHECK (length(trim(name)) > 0),
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid,
  UNIQUE (tenant_id, code)
);
ALTER TABLE sites ADD COLUMN group_id uuid REFERENCES site_groups(id);

CREATE TABLE locations (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  site_id         uuid NOT NULL REFERENCES sites(id),
  parent_id       uuid REFERENCES locations(id),
  kind            text NOT NULL CHECK (kind IN ('building', 'area', 'room')),
  code            text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,29}$'),
  name_en         text NOT NULL CHECK (length(trim(name_en)) > 0),
  name_ar         text,
  cleanroom_grade text CHECK (cleanroom_grade IN ('A', 'B', 'C', 'D', 'CNC', 'none')),
  notes           text,
  active          boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by      uuid,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      uuid,
  UNIQUE (site_id, code),
  CHECK (parent_id IS DISTINCT FROM id)
);
CREATE INDEX locations_parent_idx ON locations (parent_id);

-- A parent must be at the same site and at a higher level: building > area > room. Levels may be skipped.
CREATE OR REPLACE FUNCTION cm_location_rank(k text) RETURNS int LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE k WHEN 'building' THEN 1 WHEN 'area' THEN 2 WHEN 'room' THEN 3 END
$$;

CREATE OR REPLACE FUNCTION cm_location_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p locations;
BEGIN
  IF NEW.parent_id IS NOT NULL THEN
    SELECT * INTO p FROM locations WHERE id = NEW.parent_id;
    IF p.id IS NULL OR p.site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'The parent location must be at the same site' USING ERRCODE = 'check_violation';
    END IF;
    IF cm_location_rank(p.kind) >= cm_location_rank(NEW.kind) THEN
      RAISE EXCEPTION 'A % cannot be placed inside a %', NEW.kind, p.kind USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER locations_check BEFORE INSERT OR UPDATE ON locations FOR EACH ROW EXECUTE FUNCTION cm_location_check();

CREATE TABLE lines (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  site_id     uuid NOT NULL REFERENCES sites(id),
  location_id uuid REFERENCES locations(id),       -- the building or area the line belongs to (optional)
  code        text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,29}$'),
  name_en     text NOT NULL CHECK (length(trim(name_en)) > 0),
  name_ar     text,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid,
  UNIQUE (site_id, code)
);

-- ---------------------------------------------------------------- lists
CREATE TABLE departments (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  code        text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$'),
  name_en     text NOT NULL CHECK (length(trim(name_en)) > 0),
  name_ar     text,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid,
  UNIQUE (tenant_id, code)
);

CREATE TABLE asset_classes (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id),
  code                text NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,19}$'),
  name_en             text NOT NULL CHECK (length(trim(name_en)) > 0),
  name_ar             text,
  category            text NOT NULL CHECK (category IN ('equipment', 'instrument', 'gauge', 'sensor', 'tool',
                                                         'reference_standard', 'utility', 'facility', 'safety')),
  calibration_default boolean NOT NULL DEFAULT false,
  pm_default          boolean NOT NULL DEFAULT false,
  description         text,
  active              boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          uuid,
  UNIQUE (tenant_id, code)
);

CREATE TABLE vendors (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id),
  code                 text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$'),
  name                 text NOT NULL CHECK (length(trim(name)) > 0),
  kind                 text NOT NULL DEFAULT 'calibration_lab'
                         CHECK (kind IN ('calibration_lab', 'service_provider', 'manufacturer', 'supplier')),
  accreditation_body   text,               -- e.g. EGAC, ILAC member body
  accreditation_no     text,
  accreditation_expiry date,
  scope                text,               -- what the lab is accredited / contracted for
  address              text,
  phone                text,
  email                text,
  website              text,
  notes                text,
  active               boolean NOT NULL DEFAULT true,
  created_at           timestamptz NOT NULL DEFAULT now(),
  created_by           uuid,
  updated_at           timestamptz NOT NULL DEFAULT now(),
  updated_by           uuid,
  UNIQUE (tenant_id, code)
);

CREATE TABLE vendor_contacts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  vendor_id   uuid NOT NULL REFERENCES vendors(id),
  name        text NOT NULL CHECK (length(trim(name)) > 0),
  position    text,
  phone       text,
  email       text,
  is_primary  boolean NOT NULL DEFAULT false,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid
);
CREATE INDEX vendor_contacts_vendor_idx ON vendor_contacts (vendor_id);

-- ---------------------------------------------------------------- controlled files and signatures
CREATE TABLE files (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  sha256       text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  filename     text NOT NULL,
  mime         text NOT NULL,
  size_bytes   bigint NOT NULL CHECK (size_bytes > 0),
  page_count   integer,
  purpose      text NOT NULL DEFAULT 'attachment'
                 CHECK (purpose IN ('photo', 'nameplate', 'manual', 'certificate', 'import', 'attachment', 'export')),
  storage_key  text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  created_by   uuid,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  updated_by   uuid
);
CREATE INDEX files_sha_idx ON files (tenant_id, sha256);

CREATE TABLE signatures (
  id           bigserial PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  entity       text NOT NULL,
  record_id    uuid NOT NULL,
  record_label text NOT NULL,
  meaning      text NOT NULL,
  statement    text NOT NULL,
  reason       text,
  record_hash  text NOT NULL,           -- SHA-256 of exactly what was signed
  user_id      uuid NOT NULL,
  user_name    text NOT NULL,
  user_email   text NOT NULL,
  signed_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX signatures_record_idx ON signatures (entity, record_id, id);

-- ---------------------------------------------------------------- assets
CREATE TABLE assets (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id),
  site_id              uuid NOT NULL REFERENCES sites(id),
  code                 text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$'),
  name_en              text NOT NULL CHECK (length(trim(name_en)) > 0),
  name_ar              text,
  class_id             uuid NOT NULL REFERENCES asset_classes(id),
  parent_id            uuid REFERENCES assets(id),
  location_id          uuid REFERENCES locations(id),      -- home location
  current_location_id  uuid REFERENCES locations(id),      -- where a portable asset is now (NULL = at home)
  line_id              uuid REFERENCES lines(id),
  department_id        uuid REFERENCES departments(id),
  manufacturer         text,
  model                text,
  serial_no            text,
  asset_tag            text,                               -- fixed-asset or ERP number
  capacity             text,
  range_min            numeric,
  range_max            numeric,
  unit                 text,
  resolution           text,
  accuracy             text,
  gmp_criticality      text NOT NULL DEFAULT 'major' CHECK (gmp_criticality IN ('critical', 'major', 'minor', 'non_gmp')),
  calibration_required boolean NOT NULL DEFAULT false,
  pm_required          boolean NOT NULL DEFAULT false,
  is_portable          boolean NOT NULL DEFAULT false,
  status               text NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'in_service', 'restricted', 'standby', 'out_of_service', 'quarantined',
                                           'out_for_calibration', 'retired', 'disposed', 'transferred')),
  status_note          text,
  status_changed_at    timestamptz,
  install_date         date,
  warranty_until       date,
  supplier_vendor_id   uuid REFERENCES vendors(id),
  service_vendor_id    uuid REFERENCES vendors(id),
  photo_file_id        uuid REFERENCES files(id),
  nameplate_file_id    uuid REFERENCES files(id),
  external_ref         text,                               -- e.g. the machine code in Complanning
  notes                text,
  custom               jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at           timestamptz NOT NULL DEFAULT now(),
  created_by           uuid,
  updated_at           timestamptz NOT NULL DEFAULT now(),
  updated_by           uuid,
  UNIQUE (tenant_id, code),
  CHECK (parent_id IS DISTINCT FROM id),
  CHECK (range_min IS NULL OR range_max IS NULL OR range_min <= range_max)
);
CREATE INDEX assets_site_idx ON assets (site_id, status);
CREATE INDEX assets_parent_idx ON assets (parent_id);
CREATE INDEX assets_location_idx ON assets (location_id);
CREATE INDEX assets_class_idx ON assets (class_id);

-- Placement rules, and the lifecycle status can only change through a signed status change.
CREATE OR REPLACE FUNCTION cm_asset_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s uuid; n int := 0; cur uuid;
BEGIN
  FOREACH cur IN ARRAY ARRAY[NEW.location_id, NEW.current_location_id] LOOP
    IF cur IS NOT NULL THEN
      SELECT site_id INTO s FROM locations WHERE id = cur;
      IF s IS DISTINCT FROM NEW.site_id THEN
        RAISE EXCEPTION 'The location must be at the asset''s site' USING ERRCODE = 'check_violation';
      END IF;
    END IF;
  END LOOP;
  IF NEW.line_id IS NOT NULL AND (SELECT site_id FROM lines WHERE id = NEW.line_id) IS DISTINCT FROM NEW.site_id THEN
    RAISE EXCEPTION 'The line must be at the asset''s site' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.parent_id IS NOT NULL THEN
    IF (SELECT site_id FROM assets WHERE id = NEW.parent_id) IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'A component must be at the same site as its parent asset' USING ERRCODE = 'check_violation';
    END IF;
    -- no cycles: walk up from the new parent
    cur := NEW.parent_id;
    WHILE cur IS NOT NULL LOOP
      IF cur = NEW.id THEN
        RAISE EXCEPTION 'An asset cannot be placed inside its own component' USING ERRCODE = 'check_violation';
      END IF;
      n := n + 1;
      EXIT WHEN n > 20;
      SELECT parent_id INTO cur FROM assets WHERE id = cur;
    END LOOP;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' AND coalesce(cp_setting('app.status_change'), '') NOT IN ('signed', 'demo') THEN
      RAISE EXCEPTION 'A new asset starts as draft and is approved into service with an electronic signature'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.status_changed_at := now();
  ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
    IF coalesce(cp_setting('app.status_change'), '') NOT IN ('signed', 'demo') THEN
      RAISE EXCEPTION 'The status of an asset changes only with an electronic signature'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.status_changed_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER assets_check BEFORE INSERT OR UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION cm_asset_check();

CREATE TABLE asset_status_history (
  id            bigserial PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  asset_id      uuid NOT NULL REFERENCES assets(id),
  from_status   text,
  to_status     text NOT NULL,
  reason        text NOT NULL CHECK (length(trim(reason)) > 0),
  source        text NOT NULL DEFAULT 'signature' CHECK (source IN ('signature', 'demo_data')),
  signature_id  bigint REFERENCES signatures(id),
  user_id       uuid,
  user_name     text NOT NULL,
  changed_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (source = 'demo_data' OR signature_id IS NOT NULL)
);
CREATE INDEX asset_status_history_asset_idx ON asset_status_history (asset_id, id);

CREATE TABLE asset_files (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  asset_id    uuid NOT NULL REFERENCES assets(id),
  file_id     uuid NOT NULL REFERENCES files(id),
  kind        text NOT NULL CHECK (kind IN ('photo', 'nameplate', 'manual', 'certificate', 'drawing', 'other')),
  title       text,
  removed_at  timestamptz,
  removed_reason text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid
);
CREATE INDEX asset_files_asset_idx ON asset_files (asset_id);

-- ---------------------------------------------------------------- AI suggestions
-- What the AI proposed is kept exactly as received (hash of the raw answer); only the review fields change,
-- once, when a person accepts (with their corrections) or rejects it.
CREATE TABLE ai_suggestions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  kind             text NOT NULL CHECK (kind IN ('nameplate', 'import_mapping', 'certificate', 'manual', 'impact')),
  file_id          uuid REFERENCES files(id),
  provider         text NOT NULL,
  model            text NOT NULL,
  prompt_version   text NOT NULL,
  input_sha256     text,
  output           jsonb NOT NULL,
  output_sha256    text NOT NULL,
  status           text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'accepted', 'rejected')),
  record_entity    text,
  record_id        uuid,
  final            jsonb,
  changed_fields   jsonb,
  reviewed_by      uuid,
  reviewed_by_name text,
  reviewed_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  created_by       uuid,
  created_by_name  text
);
CREATE INDEX ai_suggestions_record_idx ON ai_suggestions (record_entity, record_id);

CREATE OR REPLACE FUNCTION cm_ai_suggestion_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'AI suggestions cannot be removed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status <> 'proposed' OR NEW.output IS DISTINCT FROM OLD.output OR NEW.model IS DISTINCT FROM OLD.model
     OR NEW.output_sha256 IS DISTINCT FROM OLD.output_sha256 OR NEW.file_id IS DISTINCT FROM OLD.file_id
     OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
    RAISE EXCEPTION 'An AI suggestion is kept as received and is reviewed only once' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ai_suggestions_guard BEFORE UPDATE OR DELETE ON ai_suggestions
  FOR EACH ROW EXECUTE FUNCTION cm_ai_suggestion_guard();

CREATE TABLE ai_usage (
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  user_id    uuid NOT NULL REFERENCES users(id),
  day        date NOT NULL,
  requests   integer NOT NULL DEFAULT 0,
  tokens     integer NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

-- ---------------------------------------------------------------- integration (Complanning and other apps)
CREATE TABLE integration_keys (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  name         text NOT NULL CHECK (length(trim(name)) > 0),
  key_hash     text NOT NULL UNIQUE,
  prefix       text NOT NULL,
  last_used_at timestamptz,
  revoked_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  created_by   uuid,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  updated_by   uuid
);

CREATE TABLE integration_events (
  id          bigserial PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  event       text NOT NULL,
  entity      text NOT NULL,
  record_id   uuid NOT NULL,
  payload     jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX integration_events_tenant_idx ON integration_events (tenant_id, id);

CREATE OR REPLACE FUNCTION cm_asset_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE ev text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    ev := 'asset.created';
  ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
    ev := 'asset.status_changed';
  ELSE
    RETURN NEW;
  END IF;
  INSERT INTO integration_events (tenant_id, event, entity, record_id, payload)
  VALUES (NEW.tenant_id, ev, 'asset', NEW.id, jsonb_build_object(
    'code', NEW.code, 'name', NEW.name_en, 'site_id', NEW.site_id, 'external_ref', NEW.external_ref,
    'status', NEW.status, 'previous_status', CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END,
    'available', NEW.status IN ('in_service', 'restricted')));
  RETURN NEW;
END $$;
CREATE TRIGGER assets_event AFTER INSERT OR UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION cm_asset_event();

-- ---------------------------------------------------------------- record rules
CREATE OR REPLACE FUNCTION cp_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% records cannot be changed or removed', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER signatures_append_only BEFORE UPDATE OR DELETE ON signatures FOR EACH ROW EXECUTE FUNCTION cp_append_only();
CREATE TRIGGER status_history_append_only BEFORE UPDATE OR DELETE ON asset_status_history FOR EACH ROW EXECUTE FUNCTION cp_append_only();
CREATE TRIGGER integration_events_append_only BEFORE UPDATE OR DELETE ON integration_events FOR EACH ROW EXECUTE FUNCTION cp_append_only();
CREATE TRIGGER files_append_only BEFORE UPDATE OR DELETE ON files FOR EACH ROW EXECUTE FUNCTION cp_append_only();

-- ---------------------------------------------------------------- stamping, audit, row-level security, privileges
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['site_groups', 'locations', 'lines', 'departments', 'asset_classes', 'vendors',
                           'vendor_contacts', 'files', 'assets', 'asset_files', 'integration_keys'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION cp_stamp()', t || '_stamp', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['site_groups', 'locations', 'lines', 'departments', 'asset_classes', 'vendors',
                           'vendor_contacts', 'files', 'assets', 'asset_files', 'asset_status_history',
                           'ai_suggestions', 'integration_keys'] LOOP
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cp_audit_row()', t || '_audit', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['site_groups', 'locations', 'lines', 'departments', 'asset_classes', 'vendors',
                           'vendor_contacts', 'files', 'signatures', 'assets', 'asset_status_history', 'asset_files',
                           'ai_suggestions', 'ai_usage', 'integration_keys', 'integration_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (cp_tenant_only(tenant_id)) WITH CHECK (cp_tenant_only(tenant_id))', t || '_tenant', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON site_groups, locations, lines, departments, asset_classes, vendors, vendor_contacts,
  assets, asset_files, ai_suggestions, ai_usage, integration_keys TO commach_app;
GRANT SELECT, INSERT ON files, signatures, asset_status_history, integration_events TO commach_app;
GRANT USAGE ON SEQUENCE signatures_id_seq, asset_status_history_id_seq, integration_events_id_seq TO commach_app;
GRANT EXECUTE ON FUNCTION cm_asset_event() TO commach_app;

-- The integration API authenticates with a company key, before any company context exists.
-- This returns only the company of a valid, unrevoked key.
CREATE OR REPLACE FUNCTION cm_integration_tenant(p_hash text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT tenant_id FROM integration_keys WHERE key_hash = p_hash AND revoked_at IS NULL
$$;
REVOKE ALL ON FUNCTION cm_integration_tenant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm_integration_tenant(text) TO commach_app;

-- ---------------------------------------------------------------- default asset classes
CREATE OR REPLACE FUNCTION cm_seed_classes(p_tenant uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  INSERT INTO asset_classes (tenant_id, code, name_en, name_ar, category, calibration_default, pm_default)
  SELECT p_tenant, c.code, c.en, c.ar, c.cat, c.cal, c.pm FROM (VALUES
    ('MACHINE',  'Production machine',            'ماكينة إنتاج',            'equipment',          false, true),
    ('EQUIP',    'Equipment',                     'معدة',                     'equipment',          false, true),
    ('CHAMBER',  'Stability / climatic chamber',  'غرفة ثبات',                'equipment',          true,  true),
    ('FRIDGE',   'Refrigerator / freezer',        'ثلاجة / مجمد',             'equipment',          true,  true),
    ('LABINST',  'Laboratory instrument',         'جهاز معملي',               'instrument',         true,  true),
    ('BALANCE',  'Balance',                       'ميزان',                    'instrument',         true,  false),
    ('LOGGER',   'Data logger',                   'مسجل بيانات',              'instrument',         true,  false),
    ('PGAUGE',   'Pressure gauge',                'مقياس ضغط',                'gauge',              true,  false),
    ('DPGAUGE',  'Differential pressure gauge',   'مقياس فرق الضغط',          'gauge',              true,  false),
    ('THERMO',   'Thermometer / hygrometer',      'ترمومتر / مقياس رطوبة',     'gauge',              true,  false),
    ('SENSOR',   'Sensor / probe',                'حساس',                     'sensor',             true,  false),
    ('TRANSMIT', 'Transmitter',                   'مرسل إشارة',               'sensor',             true,  false),
    ('TOOL',     'Tool',                          'أداة',                     'tool',               false, false),
    ('TORQUE',   'Torque wrench',                 'مفتاح عزم',                'tool',               true,  false),
    ('REFSTD',   'Reference standard',            'معيار مرجعي',              'reference_standard', true,  false),
    ('WEIGHTS',  'Standard weights',              'أوزان قياسية',             'reference_standard', true,  false),
    ('AHU',      'Air handling unit',             'وحدة مناولة الهواء',        'utility',            false, true),
    ('UTILITY',  'Utility system',                'نظام مرافق',               'utility',            false, true),
    ('FACILITY', 'Facility element',              'عنصر من المبنى',           'facility',           false, true),
    ('SAFETY',   'Safety equipment',              'معدات السلامة',            'safety',             false, true)
  ) AS c(code, en, ar, cat, cal, pm)
  ON CONFLICT (tenant_id, code) DO NOTHING
$$;

REVOKE ALL ON FUNCTION cm_seed_classes(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm_seed_classes(uuid) TO commach_app;

DO $$
DECLARE t uuid;
BEGIN
  FOR t IN SELECT id FROM tenants LOOP
    PERFORM cm_seed_classes(t);
  END LOOP;
END $$;
