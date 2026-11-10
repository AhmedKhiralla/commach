-- ComMach Phase 4: To-Do, calendar feed, notifications and e-mail, escalation with deviations, rescheduling with
-- approval, delegation and handover.

-- Who is escalated to: the head of an asset's department and the head of its site.
ALTER TABLE departments ADD COLUMN head_user_id uuid REFERENCES users(id);
ALTER TABLE sites ADD COLUMN head_user_id uuid REFERENCES users(id);

-- Personal notification settings and the calendar feed secret (only its SHA-256 is stored).
ALTER TABLE users ADD COLUMN notify jsonb NOT NULL DEFAULT '{}'::jsonb,
                  ADD COLUMN calendar_token_hash text;
CREATE UNIQUE INDEX users_calendar_token_idx ON users (calendar_token_hash) WHERE calendar_token_hash IS NOT NULL;

-- The calendar feed is opened by Google Calendar / Outlook without a session: find the user by the token.
CREATE OR REPLACE FUNCTION cm_calendar_user(p_hash text) RETURNS TABLE (user_id uuid, tenant_id uuid, full_name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, tenant_id, full_name FROM users WHERE calendar_token_hash = p_hash AND status = 'active' AND tenant_id IS NOT NULL
$$;
REVOKE ALL ON FUNCTION cm_calendar_user(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm_calendar_user(text) TO commach_app;

-- ---------------------------------------------------------------- in-app notifications and the e-mail outbox
CREATE TABLE notifications (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  user_id     uuid NOT NULL REFERENCES users(id),
  kind        text NOT NULL,          -- escalation | reschedule | delegation | handover | plan | deviation
  level       integer,
  title       text NOT NULL,
  body        text,
  link        text,
  task_id     uuid REFERENCES tasks(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz
);
CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC);

CREATE TABLE email_outbox (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  user_id     uuid REFERENCES users(id),
  to_email    text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('digest', 'escalation', 'reschedule', 'test')),
  subject     text NOT NULL,
  html        text NOT NULL,
  text_body   text NOT NULL,
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed', 'not_configured')),
  attempts    integer NOT NULL DEFAULT 0,
  last_error  text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  sent_at     timestamptz
);
CREATE INDEX email_outbox_queue_idx ON email_outbox (status, created_at) WHERE status = 'queued';

CREATE TABLE digest_log (
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  user_id   uuid NOT NULL REFERENCES users(id),
  day       date NOT NULL,
  items     integer NOT NULL,
  PRIMARY KEY (user_id, day)
);

-- ---------------------------------------------------------------- escalation and deviations
CREATE TABLE deviations (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id),
  site_id             uuid NOT NULL REFERENCES sites(id),
  asset_id            uuid NOT NULL REFERENCES assets(id),
  task_id             uuid REFERENCES tasks(id),
  number              text NOT NULL,
  title               text NOT NULL,
  description         text,
  status              text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  root_cause          text,
  actions             text,
  closed_signature_id bigint REFERENCES signatures(id),
  closed_at           timestamptz,
  closed_by_name      text,
  source              text NOT NULL DEFAULT 'normal' CHECK (source IN ('normal', 'demo')),
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          uuid,
  UNIQUE (tenant_id, number),
  CHECK (status = 'open' OR closed_signature_id IS NOT NULL OR source = 'demo')
);
CREATE OR REPLACE FUNCTION cm_deviation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR OLD.status = 'closed' THEN
    RAISE EXCEPTION 'A closed deviation cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER deviations_guard BEFORE UPDATE OR DELETE ON deviations FOR EACH ROW EXECUTE FUNCTION cm_deviation_guard();
CREATE TRIGGER deviations_demo BEFORE INSERT ON deviations FOR EACH ROW EXECUTE FUNCTION cm_demo_guard();

CREATE TABLE escalations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  task_id      uuid NOT NULL REFERENCES tasks(id),
  level        integer NOT NULL CHECK (level BETWEEN 1 AND 3),
  trigger      text NOT NULL CHECK (trigger IN ('not_accepted', 'due', 'window_end', 'deviation')),
  due_date     date NOT NULL,          -- the due date it was raised for (a reschedule starts again)
  recipients   uuid[] NOT NULL DEFAULT '{}',
  deviation_id uuid REFERENCES deviations(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (task_id, trigger, due_date)
);
CREATE INDEX escalations_task_idx ON escalations (task_id);

-- ---------------------------------------------------------------- rescheduling with approval
CREATE TABLE reschedules (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id               uuid NOT NULL REFERENCES tenants(id),
  task_id                 uuid NOT NULL REFERENCES tasks(id),
  old_due                 date NOT NULL,
  new_due                 date NOT NULL,
  within_tolerance        boolean NOT NULL,   -- inside the task's window: the supervisor approves; outside: and QA
  reason                  text NOT NULL CHECK (length(trim(reason)) >= 3),
  status                  text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'supervisor_approved', 'approved', 'rejected', 'withdrawn')),
  requested_by            uuid NOT NULL REFERENCES users(id),
  requested_by_name       text NOT NULL,
  supervisor_signature_id bigint REFERENCES signatures(id),
  supervisor_name         text,
  supervisor_at           timestamptz,
  qa_signature_id         bigint REFERENCES signatures(id),
  qa_name                 text,
  qa_at                   timestamptz,
  decision_note           text,
  created_at              timestamptz NOT NULL DEFAULT now(),
  created_by              uuid,
  updated_at              timestamptz NOT NULL DEFAULT now(),
  updated_by              uuid,
  CHECK (status <> 'approved' OR supervisor_signature_id IS NOT NULL),
  CHECK (status <> 'approved' OR within_tolerance OR qa_signature_id IS NOT NULL)
);
CREATE UNIQUE INDEX reschedules_one_open ON reschedules (task_id) WHERE status IN ('pending', 'supervisor_approved');
CREATE OR REPLACE FUNCTION cm_reschedule_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR OLD.status IN ('approved', 'rejected', 'withdrawn') THEN
    RAISE EXCEPTION 'A decided reschedule request cannot be changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER reschedules_guard BEFORE UPDATE OR DELETE ON reschedules FOR EACH ROW EXECUTE FUNCTION cm_reschedule_guard();

-- ---------------------------------------------------------------- delegation
CREATE TABLE delegations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  from_user   uuid NOT NULL REFERENCES users(id),
  to_user     uuid NOT NULL REFERENCES users(id),
  starts_on   date NOT NULL,
  ends_on     date NOT NULL,
  reason      text NOT NULL CHECK (length(trim(reason)) >= 3),
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid,
  CHECK (from_user <> to_user),
  CHECK (ends_on >= starts_on)
);
CREATE INDEX delegations_to_idx ON delegations (to_user) WHERE active;

-- ---------------------------------------------------------------- stamping, audit, RLS, privileges
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deviations', 'reschedules', 'delegations'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION cp_stamp()', t || '_stamp', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['deviations', 'reschedules', 'delegations', 'escalations'] LOOP
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION cp_audit_row()', t || '_audit', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['notifications', 'email_outbox', 'digest_log', 'deviations', 'escalations', 'reschedules', 'delegations'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (cp_tenant_only(tenant_id)) WITH CHECK (cp_tenant_only(tenant_id))', t || '_tenant', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON notifications, email_outbox, deviations, reschedules, delegations TO commach_app;
GRANT SELECT, INSERT ON digest_log TO commach_app;
GRANT SELECT, INSERT, UPDATE ON escalations TO commach_app;

-- Escalations raised by the scheduler (no signed-in user) are audited as 'ESCALATED' events too.
CREATE OR REPLACE FUNCTION cp_audit_event(
  p_action text, p_entity text, p_record_id text, p_record_label text,
  p_details jsonb, p_tenant uuid, p_user uuid, p_user_name text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF p_action NOT IN ('LOGIN', 'LOGIN_FAILED', 'LOGOUT', 'LOCKED', 'UNLOCKED',
                      'SESSION_TIMEOUT', 'PASSWORD_CHANGED', 'PASSWORD_RESET',
                      'EXPORT', 'IMPORT', 'BOOTSTRAP', 'SIGNED', 'SIGNATURE_FAILED',
                      'FILE_UPLOADED', 'FILE_DOWNLOADED', 'RUN_STARTED', 'RUN_FINISHED', 'RUN_FAILED',
                      'INTEGRITY_CHECK', 'AI_SUGGESTED', 'AI_REVIEWED', 'LABELS_PRINTED', 'SAMPLE_DATA',
                      'ESCALATED', 'EMAIL_SENT', 'EMAIL_FAILED', 'HANDOVER', 'CALENDAR_LINK') THEN
    RAISE EXCEPTION 'Unknown audit event: %', p_action;
  END IF;
  INSERT INTO audit_log (tenant_id, user_id, user_name, action, entity,
                         record_id, record_label, details)
  VALUES (p_tenant, p_user, coalesce(p_user_name, 'system'), p_action, p_entity,
          p_record_id, p_record_label, p_details);
END $$;
