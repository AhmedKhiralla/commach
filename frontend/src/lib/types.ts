// Shapes returned by the ComMach API.

export const STATUSES = ["draft", "in_service", "restricted", "standby", "out_of_service", "quarantined",
  "out_for_calibration", "retired", "disposed", "transferred"] as const;
export type AssetStatus = (typeof STATUSES)[number];
export const CATEGORIES = ["equipment", "instrument", "gauge", "sensor", "tool", "reference_standard", "utility",
  "facility", "safety"] as const;
export type Category = (typeof CATEGORIES)[number];
export const CRITICALITY = ["critical", "major", "minor", "non_gmp"] as const;
export type Criticality = (typeof CRITICALITY)[number];
export type LocationKind = "building" | "area" | "room";

export interface Counts {
  total: number; calibration: number; pm: number; critical: number; unavailable: number; draft: number;
  categories: Record<string, number>;
}

export interface LocationNode {
  id: string; site_id: string; parent_id: string | null; kind: LocationKind; code: string; name_en: string;
  name_ar: string | null; cleanroom_grade: string | null; notes: string | null; active: boolean;
  children: LocationNode[]; counts: Counts; own: number;
}

export interface Line { id: string; site_id: string; location_id: string | null; code: string; name_en: string; name_ar: string | null; active: boolean; asset_count?: number; }

export interface TreeSite {
  id: string; code: string; name: string; group_id: string | null; active: boolean; counts: Counts; unplaced: number;
  nodes: LocationNode[]; lines: Line[]; can_edit: boolean;
}
export interface TreeGroup { id: string; code: string; name: string; active: boolean; counts: Counts; }
export interface Tree { groups: TreeGroup[]; sites: TreeSite[]; }

export interface Location {
  id: string; site_id: string; parent_id: string | null; kind: LocationKind; code: string; name_en: string;
  name_ar: string | null; cleanroom_grade: string | null; notes: string | null; active: boolean;
}

export interface AssetClass {
  id: string; code: string; name_en: string; name_ar: string | null; category: Category; calibration_default: boolean;
  pm_default: boolean; description: string | null; active: boolean; asset_count: number;
}
export interface Department { id: string; code: string; name_en: string; name_ar: string | null; active: boolean; asset_count: number; }
export interface Contact { id?: string; name: string; position: string | null; phone: string | null; email: string | null; is_primary: boolean; active: boolean; }
export interface Vendor {
  id: string; code: string; name: string; kind: "calibration_lab" | "service_provider" | "manufacturer" | "supplier";
  accreditation_body: string | null; accreditation_no: string | null; accreditation_expiry: string | null;
  accreditation_expired: boolean; scope: string | null; address: string | null; phone: string | null; email: string | null;
  website: string | null; notes: string | null; active: boolean; contacts: Contact[]; asset_count: number;
}

export interface AssetRow {
  id: string; site_id: string; code: string; name_en: string; name_ar: string | null; status: AssetStatus;
  gmp_criticality: Criticality; calibration_required: boolean; pm_required: boolean; is_portable: boolean;
  manufacturer: string | null; model: string | null; serial_no: string | null; parent_id: string | null;
  location_id: string | null; photo_file_id: string | null; class_code: string; class_name_en: string; class_name_ar: string | null;
  category: Category; location_code: string | null; location_name_en: string | null; location_name_ar: string | null;
  parent_code: string | null; site_code: string; line_code: string | null; department_code: string | null;
  child_count: number; status_changed_at: string | null; updated_at: string;
}

export interface AssetFile {
  id: string; kind: string; title: string | null; created_at: string; file_id: string; filename: string; mime: string;
  size_bytes: number; sha256: string; page_count: number | null; added_by: string | null;
}

export interface HistoryRow {
  id: number; from_status: AssetStatus | null; to_status: AssetStatus; reason: string; source: "signature" | "demo_data";
  user_name: string; changed_at: string; meaning: string | null; statement: string | null; user_email: string | null;
  record_hash: string | null; signed_at: string | null;
}

export interface AiRecord {
  id: string; kind: string; model: string; prompt_version: string; status: string; changed_fields: Record<string, { ai: unknown; final: unknown }> | null;
  reviewed_by_name: string | null; reviewed_at: string | null; created_at: string; created_by_name: string | null;
}

export interface Asset {
  id: string; site_id: string; code: string; name_en: string; name_ar: string | null; class_id: string; parent_id: string | null;
  location_id: string | null; current_location_id: string | null; line_id: string | null; department_id: string | null;
  manufacturer: string | null; model: string | null; serial_no: string | null; asset_tag: string | null; capacity: string | null;
  range_min: number | null; range_max: number | null; unit: string | null; resolution: string | null; accuracy: string | null;
  gmp_criticality: Criticality; calibration_required: boolean; pm_required: boolean; is_portable: boolean;
  status: AssetStatus; status_note: string | null; status_changed_at: string | null; install_date: string | null;
  warranty_until: string | null; supplier_vendor_id: string | null; service_vendor_id: string | null;
  photo_file_id: string | null; nameplate_file_id: string | null; external_ref: string | null; notes: string | null;
  class_code: string; class_name_en: string; class_name_ar: string | null; category: Category; site_code: string;
  site_name: string; group_name: string | null; parent_code: string | null; parent_name_en: string | null; parent_name_ar: string | null;
  line_code: string | null; line_name_en: string | null; line_name_ar: string | null; department_code: string | null;
  department_name_en: string | null; department_name_ar: string | null; supplier_code: string | null; supplier_name: string | null;
  service_vendor_code: string | null; service_vendor_name: string | null; current_location_code: string | null;
  current_location_name_en: string | null; current_location_name_ar: string | null;
  location_path: { id: string; kind: LocationKind; code: string; name_en: string; name_ar: string | null }[];
  children: { id: string; code: string; name_en: string; name_ar: string | null; status: AssetStatus; calibration_required: boolean;
    pm_required: boolean; class_code: string; class_name_en: string; class_name_ar: string | null; category: Category }[];
  history: HistoryRow[]; files: AssetFile[]; ai: AiRecord[]; can_edit: boolean;
  transitions: { status: AssetStatus; can_sign: boolean }[]; updated_at: string;
}

/** Pick the name in the user's language, falling back to English. */
export function nameOf(x: { name_en: string; name_ar?: string | null }, lang: string) {
  return lang === "ar" && x.name_ar ? x.name_ar : x.name_en;
}
