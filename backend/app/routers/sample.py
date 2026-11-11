"""One-click demonstration data: a solid-dosage pharmaceutical plant with a QC laboratory, warehouse and
utilities, about 140 assets with components, realistic statuses, departments and two calibration vendors.

Loaded into the company of the administrator who asks for it, at a new demo site (or an empty site they pick).
Statuses of demo assets are set directly and recorded as "demo data" in the status history, never as signed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import BaseModel

from .. import work
from ..api import ApiError, Call, endpoint

GROUP = ("EG", "Egypt operations")
SITE = ("OCT", "6th of October plant (demo)")

DEPARTMENTS = [("PROD", "Production", "الإنتاج"), ("QC", "Quality control", "مراقبة الجودة"),
               ("QA", "Quality assurance", "توكيد الجودة"), ("ENG", "Engineering", "الهندسة"),
               ("WH", "Warehouse", "المخازن"), ("MICRO", "Microbiology", "الميكروبيولوجي")]

# (code, kind, parent, name_en, name_ar, grade)
LOCATIONS = [
    ("B1", "building", None, "Solid dosage building", "مبنى المستحضرات الصلبة", None),
    ("B1-GC", "area", "B1", "Granulation and compression", "التحبيب والكبس", "D"),
    ("B1-CT", "area", "B1", "Coating and encapsulation", "التغليف والكبسلة", "D"),
    ("B1-PK", "area", "B1", "Packaging", "التعبئة والتغليف", "CNC"),
    ("R-101", "room", "B1-GC", "Dispensing booth", "غرفة الوزن", "D"),
    ("R-102", "room", "B1-GC", "Granulation room 1", "غرفة التحبيب 1", "D"),
    ("R-103", "room", "B1-GC", "Compression room 1", "غرفة الكبس 1", "D"),
    ("R-104", "room", "B1-GC", "Compression room 2", "غرفة الكبس 2", "D"),
    ("R-105", "room", "B1-CT", "Coating room", "غرفة التغليف بالأفلام", "D"),
    ("R-106", "room", "B1-CT", "Capsule filling room", "غرفة ملء الكبسولات", "D"),
    ("R-107", "room", "B1-GC", "In-process control lab", "معمل الرقابة أثناء التشغيل", "D"),
    ("R-110", "room", "B1-PK", "Blister room 1", "غرفة البليستر 1", "D"),
    ("R-111", "room", "B1-PK", "Blister room 2", "غرفة البليستر 2", "D"),
    ("R-112", "room", "B1-PK", "Secondary packaging hall", "صالة التعبئة الثانوية", "CNC"),
    ("B2", "building", None, "QC laboratory and warehouse", "مبنى معامل الجودة والمخازن", None),
    ("B2-QC", "area", "B2", "Quality control laboratory", "معمل مراقبة الجودة", "CNC"),
    ("B2-MI", "area", "B2", "Microbiology laboratory", "معمل الميكروبيولوجي", "C"),
    ("B2-WH", "area", "B2", "Warehouse", "المخازن", "CNC"),
    ("R-201", "room", "B2-QC", "Instrument room", "غرفة الأجهزة", "CNC"),
    ("R-202", "room", "B2-QC", "Wet chemistry lab", "معمل الكيمياء", "CNC"),
    ("R-203", "room", "B2-QC", "Balance room", "غرفة الموازين", "CNC"),
    ("R-204", "room", "B2-QC", "Stability room", "غرفة الثبات", "CNC"),
    ("R-205", "room", "B2-MI", "Microbiology lab", "معمل الميكروبيولوجي", "C"),
    ("R-206", "room", "B2-QC", "Reference standards room", "غرفة المعايير المرجعية", "CNC"),
    ("R-210", "room", "B2-WH", "Raw material store", "مخزن الخامات", "CNC"),
    ("R-211", "room", "B2-WH", "Cold room", "الغرفة الباردة", "CNC"),
    ("R-212", "room", "B2-WH", "Finished goods store", "مخزن المنتج التام", "CNC"),
    ("B3", "building", None, "Utilities building", "مبنى المرافق", None),
    ("R-301", "room", "B3", "HVAC plant room", "غرفة وحدات التكييف", None),
    ("R-302", "room", "B3", "Water treatment room", "غرفة معالجة المياه", None),
    ("R-303", "room", "B3", "Compressor room", "غرفة الضواغط", None),
    ("R-304", "room", "B3", "Boiler and chiller room", "غرفة الغلاية والمبرد", None),
]

LINES = [("L-TAB1", "Tablet line 1", "خط الأقراص 1", "B1-GC"), ("L-BLS1", "Blister line 1", "خط البليستر 1", "B1-PK"),
         ("L-BLS2", "Blister line 2", "خط البليستر 2", "B1-PK")]

VENDORS = [
    ("NCS", "Nile Calibration Services (demo)", "calibration_lab", "EGAC", "CL-000-DEMO", 400,
     "Mass, temperature, pressure, humidity, dimensional; on-site and in-lab",
     [("Mona Adel (demo)", "Calibration coordinator", "+20 100 000 0001", "mona@ncs.example", True)]),
    ("DMI", "Delta Metrology Institute (demo)", "calibration_lab", "EGAC", "CL-111-DEMO", 45,
     "Electrical, force, torque, optical and analytical instruments",
     [("Karim Saeed (demo)", "Technical manager", "+20 100 000 0002", "karim@dmi.example", True)]),
    ("PMS", "Pharma Machines Service (demo)", "service_provider", None, None, None,
     "Service of tablet presses and blister machines", [("Hany Fawzy (demo)", "Service engineer", "+20 100 000 0003", "hany@pms.example", True)]),
]

# Asset rows. Fields: code, class, name_en, name_ar, room, line, dept, manufacturer, model, capacity,
# (range_min, range_max, unit), resolution, accuracy, criticality, parent, extra dict
A = []


def a(code, cls, en, ar, room, dept, mfr=None, model=None, cap=None, rng=None, res=None, acc=None, crit="major",
      parent=None, line=None, **extra):
    A.append(dict(code=code, cls=cls, en=en, ar=ar, room=room, dept=dept, mfr=mfr, model=model, cap=cap, rng=rng,
                  res=res, acc=acc, crit=crit, parent=parent, line=line, extra=extra))


# --- Solid dosage: dispensing
a("BAL-DSP-01", "BALANCE", "Platform balance 150 kg", "ميزان أرضي 150 كجم", "R-101", "PROD", "Mettler Toledo", "PBA430", "150 kg", (0, 150, "kg"), "d = 10 g", "±20 g", "critical")
a("BAL-DSP-02", "BALANCE", "Precision balance 6 kg", "ميزان دقيق 6 كجم", "R-101", "PROD", "Sartorius", "Entris II 6202", "6200 g", (0, 6200, "g"), "d = 0.01 g", "±0.02 g", "critical")
a("LAF-01", "EQUIP", "Dispensing booth (downflow)", "كابينة الوزن", "R-101", "PROD", "Esco", "Dispensing booth", None, None, None, None, "critical")
a("LAF-01-DPG", "DPGAUGE", "Dispensing booth filter DP gauge", "مقياس فرق ضغط فلتر كابينة الوزن", "R-101", "ENG", "Dwyer", "Magnehelic 2000-250PA", None, (0, 250, "Pa"), "5 Pa", "±2% FS", "major", "LAF-01")
# --- granulation
a("RMG-01", "MACHINE", "Rapid mixer granulator 300 L", "خلاط محبب سريع 300 لتر", "R-102", "PROD", "Gansons", "RMG-300", "300 L", None, None, None, "critical", line="L-TAB1")
a("RMG-01-PG", "PGAUGE", "RMG jacket pressure gauge", "مقياس ضغط جاكيت الخلاط", "R-102", "ENG", "Wika", "232.50", None, (0, 4, "bar"), "0.1 bar", "Class 1.0", "major", "RMG-01")
a("RMG-01-TT", "SENSOR", "RMG product temperature sensor", "حساس حرارة المنتج بالخلاط", "R-102", "ENG", "Endress+Hauser", "TR10 Pt100", None, (0, 100, "°C"), "0.1 °C", "±1 °C", "major", "RMG-01")
a("FBD-01", "MACHINE", "Fluid bed dryer 200 kg", "مجفف الطبقة المميعة 200 كجم", "R-102", "PROD", "Glatt", "WSG 200", "200 kg", None, None, None, "critical", line="L-TAB1")
a("FBD-01-TI", "SENSOR", "FBD inlet air temperature probe", "حساس حرارة هواء الدخول للمجفف", "R-102", "ENG", "Endress+Hauser", "TR10 Pt100", None, (0, 150, "°C"), "0.1 °C", "±1 °C", "critical", "FBD-01")
a("FBD-01-TO", "SENSOR", "FBD outlet air temperature probe", "حساس حرارة هواء الخروج للمجفف", "R-102", "ENG", "Endress+Hauser", "TR10 Pt100", None, (0, 150, "°C"), "0.1 °C", "±1 °C", "critical", "FBD-01")
a("FBD-01-DPG", "DPGAUGE", "FBD filter bag DP gauge", "مقياس فرق ضغط أكياس الفلتر", "R-102", "ENG", "Dwyer", "Magnehelic 2000-500PA", None, (0, 500, "Pa"), "10 Pa", "±2% FS", "major", "FBD-01")
a("MIL-01", "MACHINE", "Multi mill", "مطحنة متعددة", "R-102", "PROD", "Cadmach", "MM-20", None, None, None, None, "minor", line="L-TAB1")
a("BLD-01", "MACHINE", "Octagonal blender 500 L", "خلاط ثماني 500 لتر", "R-102", "PROD", "Gansons", "OB-500", "500 L", None, None, None, "critical", line="L-TAB1")
# --- compression
a("TP-01", "MACHINE", "Rotary tablet press 37 stations", "ماكينة كبس أقراص دوارة 37 محطة", "R-103", "PROD", "Fette", "P1200", "37 stations", None, None, None, "critical", line="L-TAB1")
a("TP-01-FS", "SENSOR", "TP-01 compression force sensor", "حساس قوة الكبس", "R-103", "ENG", "Fette", "Strain gauge", None, (0, 100, "kN"), "0.1 kN", "±1% FS", "critical", "TP-01")
a("TP-01-PG", "PGAUGE", "TP-01 hydraulic pressure gauge", "مقياس الضغط الهيدروليكي", "R-103", "ENG", "Wika", "213.53", None, (0, 250, "bar"), "5 bar", "Class 1.6", "major", "TP-01")
a("TP-02", "MACHINE", "Rotary tablet press 27 stations", "ماكينة كبس أقراص دوارة 27 محطة", "R-104", "PROD", "Cadmach", "CMB4-27", "27 stations", None, None, None, "critical")
a("TP-02-FS", "SENSOR", "TP-02 compression force sensor", "حساس قوة الكبس", "R-104", "ENG", "Cadmach", "Load cell", None, (0, 80, "kN"), "0.1 kN", "±1% FS", "critical", "TP-02")
a("DD-01", "EQUIP", "Tablet deduster", "منظف الأقراص من الغبار", "R-103", "PROD", "Fette", "FE-D", None, None, None, None, "minor", line="L-TAB1")
a("MD-01", "EQUIP", "Tablet metal detector", "كاشف المعادن للأقراص", "R-103", "PROD", "Lock Inspection", "Insight", None, None, None, "Fe 0.5 mm, SS 0.8 mm", "critical", line="L-TAB1", cal=True)
# --- coating and capsules
a("CP-01", "MACHINE", "Auto coater 48 inch", "ماكينة تغليف آلية 48 بوصة", "R-105", "PROD", "Gansons", "GAC-48", "150 kg", None, None, None, "critical")
a("CP-01-TI", "SENSOR", "Coater inlet air temperature probe", "حساس حرارة هواء دخول المغلف", "R-105", "ENG", "Endress+Hauser", "TR10 Pt100", None, (0, 120, "°C"), "0.1 °C", "±1 °C", "critical", "CP-01")
a("CP-01-DPG", "DPGAUGE", "Coater pan DP gauge", "مقياس فرق ضغط حلة التغليف", "R-105", "ENG", "Dwyer", "Magnehelic 2000-250PA", None, (-250, 0, "Pa"), "5 Pa", "±2% FS", "major", "CP-01")
a("CF-01", "MACHINE", "Automatic capsule filling machine", "ماكينة ملء كبسولات آلية", "R-106", "PROD", "Bosch", "GKF 1400", "84,000 caps/h", None, None, None, "critical")
# --- IPC lab
a("BAL-IPC-01", "BALANCE", "IPC analytical balance", "ميزان تحليلي للرقابة أثناء التشغيل", "R-107", "QA", "Sartorius", "Quintix 224-1S", "220 g", (0, 220, "g"), "d = 0.1 mg", "±0.2 mg", "critical")
a("HT-01", "LABINST", "Tablet hardness tester", "جهاز قياس صلابة الأقراص", "R-107", "QA", "Erweka", "TBH 125", None, (0, 500, "N"), "1 N", "±1%", "critical")
a("FR-01", "LABINST", "Friability tester", "جهاز اختبار التفتت", "R-107", "QA", "Erweka", "TAR 120", None, (0, 100, "rpm"), "1 rpm", "25 ± 1 rpm", "major")
a("DT-01", "LABINST", "Disintegration tester", "جهاز اختبار التحلل", "R-107", "QA", "Erweka", "ZT 322", None, (35, 39, "°C"), "0.1 °C", "37 ± 1 °C", "major")
a("MA-01", "LABINST", "Moisture analyzer (LOD)", "جهاز قياس الرطوبة", "R-107", "QA", "Mettler Toledo", "HC103", "120 g", (40, 230, "°C"), "0.01 %", "±0.1 %", "critical")
# --- packaging
a("BL-01", "MACHINE", "Blister packing machine 1", "ماكينة تعبئة بليستر 1", "R-110", "PROD", "Uhlmann", "B 1240", "300 blisters/min", None, None, None, "critical", line="L-BLS1")
a("BL-01-TF", "SENSOR", "BL-01 forming temperature controller", "حساس حرارة التشكيل", "R-110", "ENG", "Omron", "E5CC", None, (0, 200, "°C"), "1 °C", "±2 °C", "critical", "BL-01")
a("BL-01-TS", "SENSOR", "BL-01 sealing temperature controller", "حساس حرارة اللحام", "R-110", "ENG", "Omron", "E5CC", None, (0, 250, "°C"), "1 °C", "±2 °C", "critical", "BL-01")
a("BL-01-PG", "PGAUGE", "BL-01 compressed air pressure gauge", "مقياس ضغط الهواء المضغوط", "R-110", "ENG", "Wika", "111.10", None, (0, 10, "bar"), "0.2 bar", "Class 2.5", "minor", "BL-01")
a("BL-02", "MACHINE", "Blister packing machine 2", "ماكينة تعبئة بليستر 2", "R-111", "PROD", "Marchesini", "MB 421", "250 blisters/min", None, None, None, "critical", line="L-BLS2")
a("BL-02-TS", "SENSOR", "BL-02 sealing temperature controller", "حساس حرارة اللحام", "R-111", "ENG", "Omron", "E5CC", None, (0, 250, "°C"), "1 °C", "±2 °C", "critical", "BL-02")
a("CT-01", "MACHINE", "Cartoning machine", "ماكينة الكرتنة", "R-112", "PROD", "Uhlmann", "C 2155", "150 cartons/min", None, None, None, "major", line="L-BLS1")
a("CW-01", "EQUIP", "Checkweigher", "ميزان المراجعة الآلي", "R-112", "PROD", "Mettler Toledo", "C3570", "3 kg", (0, 3000, "g"), "0.1 g", "±0.3 g", "critical", line="L-BLS1", cal=True)
a("INK-01", "EQUIP", "Inkjet coder", "طابعة الحبر النفاث", "R-112", "PROD", "Videojet", "1580", None, None, None, None, "major", line="L-BLS1")
a("CT-02", "MACHINE", "Cartoning machine 2", "ماكينة الكرتنة 2", "R-112", "PROD", "Marchesini", "MA 305", "120 cartons/min", None, None, None, "major", line="L-BLS2")
# --- room monitoring in production
for room, en, ar in (("R-101", "Dispensing", "الوزن"), ("R-102", "Granulation", "التحبيب"), ("R-103", "Compression 1", "الكبس 1"),
                     ("R-104", "Compression 2", "الكبس 2"), ("R-105", "Coating", "التغليف"), ("R-106", "Capsule filling", "الكبسلة"),
                     ("R-110", "Blister 1", "البليستر 1"), ("R-111", "Blister 2", "البليستر 2")):
    n = room[2:]
    a(f"DPG-{n}", "DPGAUGE", f"Room DP gauge – {en}", f"مقياس فرق ضغط الغرفة – {ar}", room, "ENG", "Dwyer", "Magnehelic 2000-60PA", None, (0, 60, "Pa"), "2 Pa", "±2% FS", "major")
    a(f"THG-{n}", "THERMO", f"Room thermo-hygrometer – {en}", f"مقياس حرارة ورطوبة الغرفة – {ar}", room, "ENG", "Testo", "608-H1", None, (0, 50, "°C"), "0.1 °C / 1 %RH", "±0.5 °C / ±3 %RH", "major")
# --- QC laboratory
a("HPLC-01", "LABINST", "HPLC system 1 (DAD)", "جهاز HPLC رقم 1", "R-201", "QC", "Agilent", "1260 Infinity II", None, None, None, None, "critical")
for sub, en, ar in (("PMP", "quaternary pump", "المضخة"), ("ALS", "autosampler", "الحاقن الآلي"), ("TCC", "column oven", "فرن العمود"), ("DAD", "diode array detector", "الكاشف")):
    a(f"HPLC-01-{sub}", "LABINST", f"HPLC 1 {en}", f"HPLC 1 – {ar}", "R-201", "QC", "Agilent", "1260 Infinity II", None, None, None, None, "critical", "HPLC-01")
a("HPLC-02", "LABINST", "HPLC system 2 (UV)", "جهاز HPLC رقم 2", "R-201", "QC", "Waters", "Alliance e2695 / 2489", None, None, None, None, "critical")
a("GC-01", "LABINST", "Gas chromatograph (FID, headspace)", "جهاز كروماتوجرافيا غازية", "R-201", "QC", "Agilent", "8890 / 7697A", None, None, None, None, "critical")
a("UV-01", "LABINST", "UV-Vis spectrophotometer", "جهاز الأشعة فوق البنفسجية", "R-202", "QC", "Shimadzu", "UV-1900i", None, (190, 1100, "nm"), "0.1 nm", "±0.3 nm", "critical")
a("FTIR-01", "LABINST", "FTIR spectrometer", "جهاز الأشعة تحت الحمراء", "R-201", "QC", "Bruker", "Alpha II", None, (400, 4000, "cm-1"), "2 cm-1", "±1 cm-1", "major")
a("DS-01", "LABINST", "Dissolution tester 8 vessels", "جهاز الذوبان 8 أوعية", "R-202", "QC", "Erweka", "DT 820", None, (20, 250, "rpm"), "1 rpm", "±2 % / 37 ± 0.5 °C", "critical")
a("DS-01-TP", "SENSOR", "Dissolution bath temperature probe", "حساس حرارة حمام الذوبان", "R-202", "QC", "Erweka", "Pt100", None, (20, 45, "°C"), "0.1 °C", "±0.3 °C", "critical", "DS-01")
a("KF-01", "LABINST", "Karl Fischer titrator", "جهاز كارل فيشر", "R-202", "QC", "Metrohm", "917 Coulometer", None, None, "0.1 µg", "±3 %", "critical")
a("PH-01", "LABINST", "pH meter", "جهاز قياس الأس الهيدروجيني", "R-202", "QC", "Mettler Toledo", "SevenCompact S220", None, (0, 14, "pH"), "0.01 pH", "±0.02 pH", "critical")
a("COND-01", "LABINST", "Conductivity meter", "جهاز قياس التوصيلية", "R-202", "QC", "Mettler Toledo", "SevenCompact S230", None, (0, 500, "mS/cm"), "0.01 µS/cm", "±0.5 %", "major")
a("MP-01", "LABINST", "Melting point apparatus", "جهاز نقطة الانصهار", "R-202", "QC", "Büchi", "M-565", None, (0, 400, "°C"), "0.1 °C", "±0.3 °C", "major")
a("PL-01", "LABINST", "Polarimeter", "جهاز قياس الاستقطاب", "R-202", "QC", "Anton Paar", "MCP 150", None, (-90, 90, "°"), "0.001 °", "±0.004 °", "major")
a("OVEN-01", "EQUIP", "Hot air oven", "فرن هواء ساخن", "R-202", "QC", "Memmert", "UF110", "108 L", (20, 300, "°C"), "0.1 °C", "±1 °C", "major", cal=True)
a("OVEN-02", "EQUIP", "Hot air oven (old)", "فرن هواء ساخن (قديم)", "R-202", "QC", "Memmert", "UN55", "53 L", (20, 300, "°C"), "0.5 °C", "±2 °C", "minor", cal=True)
a("MF-01", "EQUIP", "Muffle furnace", "فرن الحرق", "R-202", "QC", "Nabertherm", "L 9/11", None, (100, 1100, "°C"), "1 °C", "±5 °C", "major", cal=True)
a("WB-01", "EQUIP", "Water bath", "حمام مائي", "R-202", "QC", "Memmert", "WNB 22", None, (10, 95, "°C"), "0.1 °C", "±0.5 °C", "minor", cal=True)
a("BAL-QC-01", "BALANCE", "Analytical balance (semi-micro)", "ميزان تحليلي شبه دقيق", "R-203", "QC", "Mettler Toledo", "XPR205", "220 g", (0, 220, "g"), "d = 0.01 mg", "±0.03 mg", "critical")
a("BAL-QC-02", "BALANCE", "Analytical balance", "ميزان تحليلي", "R-203", "QC", "Sartorius", "Cubis II MCA225S", "220 g", (0, 220, "g"), "d = 0.1 mg", "±0.2 mg", "critical")
a("BAL-QC-03", "BALANCE", "Top-loading balance", "ميزان علوي", "R-203", "QC", "Mettler Toledo", "MS3002TS", "3200 g", (0, 3200, "g"), "d = 0.01 g", "±0.02 g", "major")
a("THG-203", "THERMO", "Balance room thermo-hygrometer", "مقياس حرارة ورطوبة غرفة الموازين", "R-203", "QC", "Testo", "608-H1", None, (0, 50, "°C"), "0.1 °C / 1 %RH", "±0.5 °C / ±3 %RH", "major")
a("SC-01", "CHAMBER", "Stability chamber 25 °C / 60 % RH", "غرفة ثبات 25 °م / 60 %", "R-204", "QC", "Binder", "KBF 720", "720 L", (10, 70, "°C"), "0.1 °C", "±0.5 °C / ±3 %RH", "critical")
a("SC-01-T", "SENSOR", "SC-01 temperature sensor", "حساس الحرارة", "R-204", "QC", "Binder", "Pt100", None, (0, 70, "°C"), "0.1 °C", "±0.3 °C", "critical", "SC-01")
a("SC-01-RH", "SENSOR", "SC-01 humidity sensor", "حساس الرطوبة", "R-204", "QC", "Binder", "Capacitive", None, (10, 90, "%RH"), "0.1 %RH", "±2 %RH", "critical", "SC-01")
a("SC-02", "CHAMBER", "Stability chamber 40 °C / 75 % RH", "غرفة ثبات 40 °م / 75 %", "R-204", "QC", "Binder", "KBF 720", "720 L", (10, 70, "°C"), "0.1 °C", "±0.5 °C / ±3 %RH", "critical")
a("SC-02-T", "SENSOR", "SC-02 temperature sensor", "حساس الحرارة", "R-204", "QC", "Binder", "Pt100", None, (0, 70, "°C"), "0.1 °C", "±0.3 °C", "critical", "SC-02")
a("SC-02-RH", "SENSOR", "SC-02 humidity sensor", "حساس الرطوبة", "R-204", "QC", "Binder", "Capacitive", None, (10, 90, "%RH"), "0.1 %RH", "±2 %RH", "critical", "SC-02")
a("LOG-01", "LOGGER", "Temperature/humidity data logger 1", "مسجل بيانات حرارة ورطوبة 1", "R-204", "QC", "Testo", "174H", None, (-20, 70, "°C"), "0.1 °C", "±0.5 °C", "major", portable=True)
a("FR-QC-01", "FRIDGE", "Reference standards refrigerator", "ثلاجة المعايير المرجعية", "R-206", "QC", "Liebherr", "LKPv 6520", "600 L", (2, 8, "°C"), "0.1 °C", "±1 °C", "critical")
# --- microbiology
a("INC-01", "EQUIP", "Incubator 30–35 °C", "حضانة 30–35 °م", "R-205", "MICRO", "Memmert", "IN110", "108 L", (20, 80, "°C"), "0.1 °C", "±0.5 °C", "critical", cal=True)
a("INC-02", "EQUIP", "Incubator 20–25 °C", "حضانة 20–25 °م", "R-205", "MICRO", "Memmert", "IPP110eco", "108 L", (0, 70, "°C"), "0.1 °C", "±0.5 °C", "critical", cal=True)
a("AC-01", "EQUIP", "Laboratory autoclave", "أوتوكلاف معملي", "R-205", "MICRO", "Tuttnauer", "3870ELV", "85 L", None, None, None, "critical")
a("AC-01-TT", "SENSOR", "Autoclave chamber temperature sensor", "حساس حرارة غرفة الأوتوكلاف", "R-205", "MICRO", "Tuttnauer", "Pt100", None, (0, 140, "°C"), "0.1 °C", "±0.5 °C", "critical", "AC-01")
a("AC-01-PG", "PGAUGE", "Autoclave chamber pressure gauge", "مقياس ضغط غرفة الأوتوكلاف", "R-205", "MICRO", "Wika", "232.50", None, (-1, 3, "bar"), "0.05 bar", "Class 1.0", "critical", "AC-01")
a("BSC-01", "EQUIP", "Biosafety cabinet class II", "كابينة السلامة الحيوية", "R-205", "MICRO", "Esco", "Airstream AC2", None, None, None, None, "critical")
# --- reference standards and tools
a("WTS-01", "WEIGHTS", "E2 weight set 1 mg – 200 g", "طقم أوزان E2 من 1 مجم إلى 200 جم", "R-206", "QC", "Mettler Toledo", "E2 set", None, (0.001, 200, "g"), None, "OIML E2", "critical", portable=True)
a("WTS-02", "WEIGHTS", "F1 weight set 1 g – 5 kg", "طقم أوزان F1 من 1 جم إلى 5 كجم", "R-206", "QC", "Kern", "F1 set", None, (1, 5000, "g"), None, "OIML F1", "critical", portable=True)
a("WTS-03", "WEIGHTS", "M1 test weights 20 kg (x5)", "أوزان اختبار M1 20 كجم", "R-210", "WH", "Kern", "M1 20 kg", None, (20, 100, "kg"), None, "OIML M1", "major", portable=True)
a("RTD-REF-01", "REFSTD", "Reference thermometer", "ترمومتر مرجعي", "R-206", "QC", "Fluke", "1524 + 5615", None, (-200, 420, "°C"), "0.001 °C", "±0.02 °C", "critical", portable=True)
a("HYG-REF-01", "REFSTD", "Reference thermo-hygrometer", "مقياس حرارة ورطوبة مرجعي", "R-206", "QC", "Vaisala", "HM70", None, (0, 100, "%RH"), "0.1 %RH", "±1 %RH", "critical", portable=True)
a("PG-REF-01", "REFSTD", "Reference pressure calibrator", "معاير ضغط مرجعي", "R-206", "ENG", "Fluke", "719Pro", None, (-0.8, 20, "bar"), "0.001 bar", "±0.025 %", "critical", portable=True)
a("TQ-01", "TORQUE", "Torque wrench 20–100 N·m", "مفتاح عزم 20–100 نيوتن متر", "R-104", "ENG", "Norbar", "TTi 100", None, (20, 100, "N·m"), "1 N·m", "±4 %", "major", portable=True)
a("CAL-01", "TOOL", "Digital vernier caliper", "قدمة ذات ورنية رقمية", "R-107", "QA", "Mitutoyo", "500-196-30", None, (0, 150, "mm"), "0.01 mm", "±0.02 mm", "major", portable=True, cal=True)
# --- warehouse
a("BAL-WH-01", "BALANCE", "Floor scale 600 kg", "ميزان أرضي 600 كجم", "R-210", "WH", "Mettler Toledo", "PFA779", "600 kg", (0, 600, "kg"), "d = 0.1 kg", "±0.2 kg", "major")
a("THG-210", "THERMO", "RM store thermo-hygrometer", "مقياس حرارة ورطوبة مخزن الخامات", "R-210", "WH", "Testo", "608-H1", None, (0, 50, "°C"), "0.1 °C / 1 %RH", "±0.5 °C / ±3 %RH", "major")
a("CR-01", "FRIDGE", "Cold room 2–8 °C", "غرفة باردة 2–8 °م", "R-211", "WH", "Carrier", "Cold room 40 m³", "40 m³", (2, 8, "°C"), "0.1 °C", "±1 °C", "critical")
a("CR-01-T1", "SENSOR", "Cold room temperature sensor 1", "حساس حرارة الغرفة الباردة 1", "R-211", "WH", "Testo", "Saveris T1", None, (-30, 50, "°C"), "0.1 °C", "±0.5 °C", "critical", "CR-01")
a("CR-01-T2", "SENSOR", "Cold room temperature sensor 2", "حساس حرارة الغرفة الباردة 2", "R-211", "WH", "Testo", "Saveris T1", None, (-30, 50, "°C"), "0.1 °C", "±0.5 °C", "critical", "CR-01")
a("LOG-02", "LOGGER", "Shipping data logger 2", "مسجل بيانات الشحن 2", "R-212", "WH", "Testo", "184 T3", None, (-35, 70, "°C"), "0.1 °C", "±0.5 °C", "major", portable=True)
a("LOG-03", "LOGGER", "Shipping data logger 3", "مسجل بيانات الشحن 3", "R-212", "WH", "Testo", "184 T3", None, (-35, 70, "°C"), "0.1 °C", "±0.5 °C", "major", portable=True)
a("THG-212", "THERMO", "FG store thermo-hygrometer", "مقياس حرارة ورطوبة مخزن المنتج التام", "R-212", "WH", "Testo", "608-H1", None, (0, 50, "°C"), "0.1 °C / 1 %RH", "±0.5 °C / ±3 %RH", "major")
a("FL-01", "EQUIP", "Electric forklift", "رافعة شوكية كهربائية", "R-212", "WH", "Toyota", "8FBE15", "1.5 t", None, None, None, "non_gmp")
# --- utilities
for n, en, ar, rooms in ((1, "OSD granulation", "التحبيب", "R-101–R-104"), (2, "OSD coating and packaging", "التغليف والتعبئة", "R-105–R-112"),
                         (3, "QC laboratory", "معامل الجودة", "R-201–R-206"), (4, "Microbiology", "الميكروبيولوجي", "R-205")):
    a(f"AHU-0{n}", "AHU", f"AHU-0{n} {en}", f"وحدة مناولة الهواء {n} – {ar}", "R-301", "ENG", "Carrier", "39HQ", f"Serves {rooms}", None, None, None, "critical")
    a(f"AHU-0{n}-DP1", "DPGAUGE", f"AHU-0{n} pre-filter DP gauge", "مقياس فرق ضغط الفلتر الأولي", "R-301", "ENG", "Dwyer", "Magnehelic 2000-250PA", None, (0, 250, "Pa"), "5 Pa", "±2% FS", "major", f"AHU-0{n}")
    a(f"AHU-0{n}-DP2", "DPGAUGE", f"AHU-0{n} fine filter DP gauge", "مقياس فرق ضغط الفلتر الدقيق", "R-301", "ENG", "Dwyer", "Magnehelic 2000-500PA", None, (0, 500, "Pa"), "10 Pa", "±2% FS", "major", f"AHU-0{n}")
a("PW-01", "UTILITY", "Purified water generation and loop", "نظام إنتاج وتوزيع المياه النقية", "R-302", "ENG", "Christ", "Osmotron + loop", "2 m³/h", None, None, None, "critical")
a("PW-01-CS", "SENSOR", "PW loop conductivity sensor", "حساس توصيلية خط المياه النقية", "R-302", "ENG", "Mettler Toledo", "UniCond 2-e", None, (0, 20, "µS/cm"), "0.001 µS/cm", "±2 %", "critical", "PW-01")
a("PW-01-TOC", "LABINST", "PW online TOC analyzer", "محلل الكربون العضوي الكلي", "R-302", "ENG", "Sievers", "M500", None, (0.03, 1000, "ppb"), "0.01 ppb", "±2 %", "critical", "PW-01")
a("PW-01-TT", "SENSOR", "PW loop temperature sensor", "حساس حرارة خط المياه النقية", "R-302", "ENG", "Endress+Hauser", "TM411", None, (0, 100, "°C"), "0.1 °C", "±0.5 °C", "major", "PW-01")
a("PW-01-PG", "PGAUGE", "PW loop return pressure gauge", "مقياس ضغط راجع خط المياه", "R-302", "ENG", "Wika", "232.50", None, (0, 6, "bar"), "0.1 bar", "Class 1.0", "major", "PW-01")
a("PW-01-UV", "EQUIP", "PW loop UV lamp", "لمبة الأشعة فوق البنفسجية بالخط", "R-302", "ENG", "Wedeco", "Spektron 10", None, None, None, None, "major", "PW-01")
a("CA-01", "UTILITY", "Oil-free air compressor", "ضاغط هواء خالي من الزيت", "R-303", "ENG", "Atlas Copco", "ZT 37", "6 m³/min", None, None, None, "critical")
a("CA-01-PG", "PGAUGE", "Compressor discharge pressure gauge", "مقياس ضغط خروج الضاغط", "R-303", "ENG", "Wika", "232.50", None, (0, 16, "bar"), "0.5 bar", "Class 1.0", "major", "CA-01")
a("CA-01-DP", "SENSOR", "Air dryer dew point sensor", "حساس نقطة الندى", "R-303", "ENG", "Vaisala", "DMT143", None, (-70, 60, "°C td"), "0.1 °C", "±2 °C", "critical", "CA-01")
a("DRY-01", "UTILITY", "Compressed air dryer", "مجفف الهواء المضغوط", "R-303", "ENG", "Atlas Copco", "CD 25+", None, None, None, None, "major")
a("CH-01", "UTILITY", "Air-cooled chiller", "مبرد مياه", "R-304", "ENG", "Carrier", "30RB 160", "160 kW", None, None, None, "major")
a("BLR-01", "UTILITY", "Steam boiler", "غلاية بخار", "R-304", "ENG", "Bosch", "UL-S 2000", "2 t/h", None, None, None, "critical")
a("BLR-01-PG", "PGAUGE", "Boiler steam pressure gauge", "مقياس ضغط بخار الغلاية", "R-304", "ENG", "Wika", "232.50", None, (0, 16, "bar"), "0.5 bar", "Class 1.0", "critical", "BLR-01")
a("BLR-01-SV", "SAFETY", "Boiler safety valve", "صمام أمان الغلاية", "R-304", "ENG", "Leser", "441", "Set 10 bar", None, None, None, "critical", "BLR-01", cal=True)
a("DG-01", "UTILITY", "Diesel generator 500 kVA", "مولد ديزل 500 ك.ف.أ", "R-304", "ENG", "Cummins", "C550 D5", "500 kVA", None, None, None, "minor")
# --- facility and safety
for b, en, ar in (("B1", "Solid dosage building", "مبنى المستحضرات الصلبة"), ("B2", "QC and warehouse building", "مبنى الجودة والمخازن"),
                  ("B3", "Utilities building", "مبنى المرافق")):
    a(f"FAC-{b}", "FACILITY", f"{en} – civil and finishes", f"{ar} – الأعمال المدنية والتشطيبات", None, "ENG", None, None, None, None, None, None, "major", building=b)
for i, (room, kind) in enumerate((("R-112", "CO2 5 kg"), ("R-202", "CO2 5 kg"), ("R-210", "Dry powder 12 kg"), ("R-304", "Dry powder 12 kg")), start=1):
    a(f"FE-0{i}", "SAFETY", f"Fire extinguisher {kind}", f"طفاية حريق {kind}", room, "ENG", None, None, kind, None, None, None, "non_gmp")
a("EW-01", "SAFETY", "Emergency eyewash and shower", "دش وغسيل عيون للطوارئ", "R-202", "ENG", "Haws", "8300", None, None, None, None, "non_gmp")
a("FA-01", "SAFETY", "Fire alarm panel", "لوحة إنذار الحريق", "R-301", "ENG", "Notifier", "NFS2-640", None, None, None, None, "non_gmp")

# Demo statuses (anything not listed is in service; drafts were registered but not approved yet)
STATUS = {
    "FTIR-01": ("draft", None), "LOG-03": ("draft", None), "FE-04": ("draft", None), "CT-02": ("draft", None),
    "TP-02": ("out_of_service", "Main drive belt broken; repair work order raised with Pharma Machines Service."),
    "BAL-QC-02": ("out_for_calibration", "Sent to Delta Metrology Institute for annual calibration."),
    "RTD-REF-01": ("out_for_calibration", "At Nile Calibration Services for annual calibration."),
    "PH-01": ("quarantined", "Slope 92 % at the 3-point check (limit 95–105 %); electrode under investigation."),
    "MIL-01": ("standby", "Not used in the current production campaign."),
    "HPLC-02": ("restricted", "Column oven fails above 40 °C: use only for methods at 40 °C or below."),
    "OVEN-02": ("retired", "Replaced by OVEN-01."),
}


class SampleIn(BaseModel):
    site_id: str | None = None
    give_me_roles: bool = False


@endpoint(auth="tenant_admin", body=SampleIn)
def load_sample(call: Call):
    tx, b = call.tx, call.body
    tid = call.tenant_id
    if b.site_id:
        site = tx.one("SELECT id, code FROM sites WHERE id = %s::uuid", b.site_id)
        if not site:
            raise ApiError(404, "not_found", "Site was not found.")
        if tx.val("SELECT 1 FROM assets WHERE site_id = %s LIMIT 1", site["id"]) or \
                tx.val("SELECT 1 FROM locations WHERE site_id = %s LIMIT 1", site["id"]):
            raise ApiError(409, "not_empty", "Choose a site with no assets or locations yet, or let ComMach create a demo site.")
        sid = str(site["id"])
    else:
        code, n = SITE[0], 1
        while tx.val("SELECT 1 FROM sites WHERE code = %s", code):
            n += 1
            code = f"{SITE[0]}{n}"
        gid = tx.val("SELECT id FROM site_groups WHERE code = %s", GROUP[0]) or \
            tx.val("INSERT INTO site_groups (tenant_id, code, name) VALUES (%s, %s, %s) RETURNING id", tid, *GROUP)
        sid = str(tx.val("INSERT INTO sites (tenant_id, code, name, group_id, address) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                         tid, code, SITE[1], gid, "6th of October City, Giza, Egypt"))
    tx.x("SELECT cm_seed_classes(%s::uuid)", tid)
    classes = {r["code"]: r for r in tx.q("SELECT id, code, calibration_default, pm_default FROM asset_classes")}
    depts = {}
    for code, en, ar in DEPARTMENTS:
        depts[code] = tx.val("SELECT id FROM departments WHERE code = %s", code) or \
            tx.val("INSERT INTO departments (tenant_id, code, name_en, name_ar) VALUES (%s, %s, %s, %s) RETURNING id", tid, code, en, ar)
    locs = {}
    for code, kind, parent, en, ar, grade in LOCATIONS:
        locs[code] = tx.val("INSERT INTO locations (tenant_id, site_id, parent_id, kind, code, name_en, name_ar, cleanroom_grade)"
                            " VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s) RETURNING id",
                            tid, sid, locs.get(parent), kind, code, en, ar, grade)
    lines = {}
    for code, en, ar, loc in LINES:
        lines[code] = tx.val("INSERT INTO lines (tenant_id, site_id, location_id, code, name_en, name_ar)"
                             " VALUES (%s, %s::uuid, %s, %s, %s, %s) RETURNING id", tid, sid, locs[loc], code, en, ar)
    today = dt.date.today()
    vendors = {}
    for code, name, kind, body, accno, days, scope, contacts in VENDORS:
        vid = tx.val("SELECT id FROM vendors WHERE code = %s", code)
        if not vid:
            vid = tx.val("INSERT INTO vendors (tenant_id, code, name, kind, accreditation_body, accreditation_no,"
                         " accreditation_expiry, scope, phone, email) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                         tid, code, name, kind, body, accno, today + dt.timedelta(days=days) if days else None, scope,
                         contacts[0][2], contacts[0][3])
            for cname, pos, phone, email, primary in contacts:
                tx.x("INSERT INTO vendor_contacts (tenant_id, vendor_id, name, position, phone, email, is_primary)"
                     " VALUES (%s, %s, %s, %s, %s, %s, %s)", tid, vid, cname, pos, phone, email, primary)
        vendors[code] = vid
    if tx.val("SELECT 1 FROM assets WHERE code = ANY(%s::text[]) LIMIT 1", [x["code"] for x in A]):
        raise ApiError(409, "not_empty", "Demo assets are already in this company. Each company can load the demo once.")

    tx.x("SELECT set_config('app.status_change', 'demo', true)")
    ids: dict[str, str] = {}
    by_code = {x["code"]: x for x in A}
    for i, x in enumerate(A):
        cls = classes[x["cls"]]
        parent = by_code.get(x["parent"]) if x["parent"] else None
        room = x["room"] or (parent["room"] if parent else None) or x["extra"].get("building")
        status, note = STATUS.get(x["code"], ("in_service", None))
        cal = cls["calibration_default"] or bool(x["extra"].get("cal"))
        pm = cls["pm_default"]
        rng = x["rng"] or (None, None, None)
        serial = None if x["cls"] in ("FACILITY",) else f"SN{(i * 7919) % 900000 + 100000}"
        install = today.replace(day=1) - dt.timedelta(days=200 + (i * 53) % 2600)
        vendor = vendors["DMI"] if x["cls"] in ("LABINST", "BALANCE", "TORQUE", "TOOL") else \
            vendors["NCS"] if cal else vendors["PMS"] if x["cls"] == "MACHINE" else None
        aid = tx.val(
            "INSERT INTO assets (tenant_id, site_id, code, name_en, name_ar, class_id, parent_id, location_id, line_id,"
            " department_id, manufacturer, model, serial_no, capacity, range_min, range_max, unit, resolution, accuracy,"
            " gmp_criticality, calibration_required, pm_required, is_portable, status, status_note, install_date,"
            " service_vendor_id, external_ref)"
            " VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s) RETURNING id",
            tid, sid, x["code"], x["en"], x["ar"], cls["id"], ids.get(x["parent"]) if x["parent"] else None,
            locs.get(room), lines.get(x["line"]) if x["line"] else (lines.get(parent["line"]) if parent and parent["line"] else None),
            depts[x["dept"]], x["mfr"], x["model"], serial, x["cap"], rng[0], rng[1], rng[2], x["res"], x["acc"], x["crit"],
            cal, pm, bool(x["extra"].get("portable")), status, note, install, vendor,
            x["code"] if x["cls"] == "MACHINE" else None)
        ids[x["code"]] = str(aid)
        if status != "draft":
            tx.x("INSERT INTO asset_status_history (tenant_id, asset_id, from_status, to_status, reason, source, user_id, user_name)"
                 " VALUES (%s, %s, NULL, %s, %s, 'demo_data', %s, %s)", tid, aid, status,
                 note or "Loaded as demonstration data", call.user["id"], call.user["full_name"])
    cal_counts = load_calibration(call, sid, ids, vendors, today)
    cal_counts.update(load_pm(call, sid, ids, vendors, today))
    cal_counts["plans"] = load_plans(call, sid, today)
    cal_counts["forms"] = load_forms(call)
    cal_counts.update(load_phase6(call, sid, ids, today))
    tx.x("SELECT set_config('app.status_change', '', true)")
    if b.give_me_roles:
        for role in ("qa_approver", "engineering_manager", "qa_calibration_officer", "engineering_supervisor"):
            tx.x("INSERT INTO user_site_roles (tenant_id, user_id, site_id, role) VALUES (%s, %s, %s::uuid, %s)"
                 " ON CONFLICT DO NOTHING", tid, call.user["id"], sid, role)
    tx.event("SAMPLE_DATA", "sites", sid, "Demo plant",
             {"assets": len(A), "locations": len(LOCATIONS), "lines": len(LINES), "vendors": len(VENDORS), **cal_counts})
    return {"site_id": sid, "assets": len(A), "locations": len(LOCATIONS), "lines": len(LINES),
            "departments": len(DEPARTMENTS), "vendors": len(VENDORS), **cal_counts}


# ================================================================== calibration demo
# (code, title, points [(parameter, nominal, unit, mode, tol)], method)
TEMPLATES = [
    ("CAL-BAL-ANA", "Analytical balance 220 g", [("Mass", 10, "g", "abs", 0.0002), ("Mass", 50, "g", "abs", 0.0003),
                                                  ("Mass", 100, "g", "abs", 0.0005), ("Mass", 200, "g", "abs", 0.0008)], "SOP-QC-021"),
    ("CAL-BAL-PREC", "Precision / top-loading balance", [("Mass", 100, "g", "abs", 0.02), ("Mass", 1000, "g", "abs", 0.05),
                                                         ("Mass", 3000, "g", "abs", 0.1)], "SOP-QC-022"),
    ("CAL-BAL-FLOOR", "Platform and floor scales", [("Mass", 20, "kg", "pct", 0.1), ("Mass", 100, "kg", "pct", 0.1)], "SOP-ENG-031"),
    ("CAL-PH", "pH meter 3-point", [("pH buffer", 4.01, "pH", "abs", 0.05), ("pH buffer", 7.00, "pH", "abs", 0.05),
                                    ("pH buffer", 10.01, "pH", "abs", 0.05)], "SOP-QC-030"),
    ("CAL-HPLC", "HPLC performance check", [("Flow rate", 1.000, "mL/min", "pct", 2), ("Wavelength accuracy", 486.0, "nm", "abs", 1),
                                            ("Column oven", 40.0, "°C", "abs", 2)], "SOP-QC-040"),
    ("CAL-TEMP", "Process temperature probe", [("Temperature", 25, "°C", "abs", 1.0), ("Temperature", 70, "°C", "abs", 1.0),
                                               ("Temperature", 120, "°C", "abs", 1.0)], "SOP-ENG-041"),
    ("CAL-TEMP-LAB", "Laboratory temperature sensor", [("Temperature", 5, "°C", "abs", 0.5), ("Temperature", 25, "°C", "abs", 0.5),
                                                       ("Temperature", 40, "°C", "abs", 0.5)], "SOP-QC-042"),
    ("CAL-RH", "Humidity sensor", [("Relative humidity", 33, "%RH", "abs", 3), ("Relative humidity", 75, "%RH", "abs", 3)], "SOP-QC-043"),
    ("CAL-THG", "Thermo-hygrometer", [("Temperature", 25, "°C", "abs", 0.5), ("Relative humidity", 50, "%RH", "abs", 3)], "SOP-ENG-044"),
    ("CAL-PG-LOW", "Pressure gauge up to 10 bar", [("Pressure", 2, "bar", "abs", 0.16), ("Pressure", 5, "bar", "abs", 0.16),
                                                   ("Pressure", 8, "bar", "abs", 0.16)], "SOP-ENG-051"),
    ("CAL-DPG-60", "Room DP gauge 0–60 Pa", [("Differential pressure", 10, "Pa", "abs", 2), ("Differential pressure", 30, "Pa", "abs", 2),
                                             ("Differential pressure", 50, "Pa", "abs", 2)], "SOP-ENG-052"),
    ("CAL-DPG-250", "Filter DP gauge 0–250 Pa", [("Differential pressure", 50, "Pa", "abs", 5), ("Differential pressure", 125, "Pa", "abs", 5),
                                                 ("Differential pressure", 200, "Pa", "abs", 5)], "SOP-ENG-053"),
]

SPECIAL_DUE = {  # days from today for the open task (negative = overdue)
    "FBD-01-TI": -25, "DPG-104": -14, "THG-111": -40,
    "BAL-IPC-01": 3, "THG-103": 5, "LOG-01": 6, "PH-01": 2, "DS-01-TP": 9, "WTS-02": 12,
}


def _h(code: str) -> float:
    return int(hashlib.sha256(code.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _template_for(x: dict) -> str | None:
    code, cls, rng = x["code"], x["cls"], x["rng"] or (None, None, None)
    unit, hi = rng[2], rng[1]
    if cls == "BALANCE":
        return "CAL-BAL-ANA" if unit == "g" and (hi or 0) <= 220 else "CAL-BAL-PREC" if unit == "g" else "CAL-BAL-FLOOR"
    if code == "PH-01":
        return "CAL-PH"
    if code in ("HPLC-01", "HPLC-02"):
        return "CAL-HPLC"
    if cls == "THERMO":
        return "CAL-THG"
    if cls == "DPGAUGE":
        return "CAL-DPG-60" if hi == 60 else "CAL-DPG-250" if hi == 250 else None
    if cls == "PGAUGE":
        return "CAL-PG-LOW" if hi is not None and hi <= 10 else None
    if unit == "%RH":
        return "CAL-RH"
    if unit == "°C" and cls in ("SENSOR", "LOGGER"):
        return "CAL-TEMP-LAB" if (hi or 0) <= 70 else "CAL-TEMP"
    return None


def _plan(x: dict) -> tuple[int, str, str, str | None]:
    """interval value, unit, performer, vendor code"""
    cls = x["cls"]
    if cls in ("WEIGHTS", "REFSTD", "TORQUE"):
        return 1, "year", "external", "NCS"
    if cls in ("BALANCE", "LABINST", "TOOL") or x["code"] in ("MD-01", "CW-01"):
        return 1, "year", "external", "DMI"
    if cls in ("SAFETY",):
        return 1, "year", "external", "NCS"
    if cls in ("DPGAUGE", "THERMO", "SENSOR", "LOGGER", "PGAUGE", "TRANSMIT"):
        return 6, "month", "internal", None
    return 1, "year", "external", "NCS"   # chambers, fridges, ovens, incubators: temperature mapping by the lab


def load_calibration(call: Call, sid: str, ids: dict[str, str], vendors: dict[str, str], today: dt.date) -> dict:
    tx, tid = call.tx, call.tenant_id
    settings = work.effective_settings(tx.val("SELECT work_settings FROM tenants WHERE id = %s", tid))
    classes = {r["code"]: r["id"] for r in tx.q("SELECT id, code FROM asset_classes")}
    templates: dict[str, dict] = {}
    for code, title, points, method in TEMPLATES:
        t = tx.one("INSERT INTO templates (tenant_id, kind, code, title, method_ref, status, source, standards_note)"
                   " VALUES (%s, 'calibration', %s, %s, %s, 'draft', 'demo', %s) RETURNING id", tid, code, title, method,
                   "Traceable reference standard with a valid certificate; test uncertainty ratio 4:1 or better")
        items = []
        for n, (param, nominal, unit, mode, tol) in enumerate(points, start=1):
            iid = tx.val("INSERT INTO template_items (tenant_id, template_id, seq, item_type, parameter, nominal, unit, tol_mode,"
                         " tol_minus, tol_plus) VALUES (%s, %s, %s, 'point', %s, %s, %s, %s, %s, %s) RETURNING id",
                         tid, t["id"], n, param, nominal, unit, mode, tol, tol)
            item = {"id": iid, "seq": n, "parameter": param, "nominal": nominal, "unit": unit, "tol_mode": mode,
                    "tol_minus": tol, "tol_plus": tol}
            item["low"], item["high"] = work.limits(item)
            items.append(item)
        tx.x("UPDATE templates SET status = 'approved', approved_at = now(), approved_by_name = 'QA approver (demo)'"
             " WHERE id = %s", t["id"])
        templates[code] = {"id": t["id"], "items": items}

    std_for = {"°C": "RTD-REF-01", "%RH": "HYG-REF-01", "bar": "PG-REF-01", "Pa": "PG-REF-01", "g": "WTS-01", "kg": "WTS-03"}
    counters: dict[str, int] = {}

    def number(year: int) -> str:
        counters[year] = counters.get(year, 0) + 1
        return f"CAL-{year}-{counters[year]:04d}"

    n_tasks = n_sched = 0
    impacts = []
    for x in A:
        status = STATUS.get(x["code"], ("in_service", None))[0]
        if status in ("draft", "retired"):
            continue
        cls_cal = x["cls"] in ("CHAMBER", "FRIDGE", "LABINST", "BALANCE", "LOGGER", "PGAUGE", "DPGAUGE", "THERMO", "SENSOR",
                               "TRANSMIT", "TORQUE", "REFSTD", "WEIGHTS")
        if not (cls_cal or x["extra"].get("cal")):
            continue
        aid = ids[x["code"]]
        if x["parent"] and x["cls"] == "LABINST":
            continue   # HPLC modules are calibrated with their system
        value, unit, performer, vcode = _plan(x)
        tcode = _template_for(x)
        tpl = templates.get(tcode) if tcode else None
        sch = {"kind": "calibration", "interval_value": value, "interval_unit": unit, "basis": "floating",
               "tol_before_value": None, "tol_before_unit": None, "tol_after_value": None, "tol_after_unit": None}
        idays = work.interval_days(value, unit)
        # spread the next due dates across the coming interval; a few are overdue or due this week on purpose
        if x["code"] in SPECIAL_DUE:
            next_due = today + dt.timedelta(days=SPECIAL_DUE[x["code"]])
        else:
            next_due = today + dt.timedelta(days=int(14 + _h(x["code"]) * (idays - 20)))
        last_done = _back(next_due, value, unit)
        s_id = tx.val(
            "INSERT INTO schedules (tenant_id, site_id, asset_id, kind, template_id, interval_value, interval_unit, basis,"
            " performer, vendor_id, assignee_roles, certificate_required, last_done, next_due)"
            " VALUES (%s, %s, %s, 'calibration', %s, %s, %s, 'floating', %s, %s, %s::text[], %s, %s, %s) RETURNING id",
            tid, sid, aid, tpl["id"] if tpl else None, value, unit, performer, vendors.get(vcode) if vcode else None,
            [] if performer == "external" else ["qa_calibration_officer"], performer == "external", last_done, next_due)
        n_sched += 1
        # history: completed calibrations going back about two years
        done_dates = []
        d = last_done
        while d > today - dt.timedelta(days=760) and len(done_dates) < 4:
            done_dates.append(d)
            d = _back(d, value, unit)
        for k, performed in enumerate(reversed(done_dates)):
            due = performed + dt.timedelta(days=int((_h(x["code"] + str(k)) - 0.6) * 8))
            start, end = work.window(due, sch, settings)
            latest = k == len(done_dates) - 1
            verdict = "pass"
            if latest and x["code"] == "PH-01":
                verdict = "fail"
            if latest and x["code"] == "SC-02-RH":
                verdict = "adjusted"
            vname = next((v[1] for v in VENDORS if v[0] == vcode), None) if vcode else None
            t = tx.one(
                "INSERT INTO tasks (tenant_id, site_id, asset_id, schedule_id, kind, number, template_id, due_date, window_start,"
                " window_end, status, performer, vendor_id, assignee_roles, certificate_required, accepted_at, accepted_by_name,"
                " performed_date, performed_by_name, certificate_no, env_temperature, env_humidity, verdict, completed_at,"
                " completed_by_name, reviewed_at, reviewed_by_name, source)"
                " VALUES (%s, %s, %s, %s, 'calibration', %s, %s, %s, %s, %s, 'completed', %s, %s, %s::text[], %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, %s, %s, %s, %s, 'demo') RETURNING id",
                tid, sid, aid, s_id, number(due.year), tpl["id"] if tpl else None, due, start, end, performer,
                vendors.get(vcode) if vcode else None, [] if performer == "external" else ["qa_calibration_officer"],
                performer == "external", dt.datetime.combine(performed, dt.time(8, 30)), "Calibration officer (demo)",
                performed, f"{vname} technician (demo)" if vname else "Calibration officer (demo)",
                f"{vcode}-{performed.year}-{int(_h(x['code'] + str(k)) * 9000) + 1000}" if vcode else None,
                f"{21 + _h(x['code'] + 't' + str(k)) * 2:.1f} °C", f"{42 + int(_h(x['code'] + 'h' + str(k)) * 12)} %RH",
                verdict, dt.datetime.combine(performed, dt.time(14, 0)), "Calibration officer (demo)",
                dt.datetime.combine(performed + dt.timedelta(days=1), dt.time(10, 0)) if not latest or verdict == "pass" else None,
                "QA approver (demo)" if not latest or verdict == "pass" else None)
            n_tasks += 1
            if tpl:
                for it in tpl["items"]:
                    half = (it["high"] - it["low"]) / 2
                    drift = decimal_(0.08 + 0.12 * k + (_h(x["code"] + str(it["seq"])) - 0.5) * 0.3)
                    found = decimal_(it["nominal"]) + half * drift
                    left = None
                    if verdict in ("fail", "adjusted") and it["seq"] == 2:
                        found = found + half * decimal_(1.6)
                        left = (decimal_(it["nominal"]) + half * decimal_(0.1)) if verdict == "adjusted" else found
                    tx.x("INSERT INTO task_results (tenant_id, task_id, item_id, seq, item_type, parameter, nominal, unit,"
                         " low_limit, high_limit, as_found, as_left, uncertainty, ok_found, ok_left)"
                         " VALUES (%s, %s, %s, %s, 'point', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         tid, t["id"], it["id"], it["seq"], it["parameter"], it["nominal"], it["unit"], it["low"], it["high"],
                         round(found, 6), round(left, 6) if left is not None else None, round(half / 5, 6),
                         it["low"] <= found <= it["high"], (it["low"] <= left <= it["high"]) if left is not None else None)
                std = std_for.get(x["rng"][2] if x["rng"] else "", None) if performer == "internal" else None
                if std and std in ids:
                    tx.x("INSERT INTO task_standards (tenant_id, task_id, standard_asset_id, description, cert_ref, valid_until)"
                         " VALUES (%s, %s, %s, %s, %s, %s)", tid, t["id"], ids[std], std,
                         f"NCS-{performed.year}-{int(_h(std) * 900) + 100}", performed + dt.timedelta(days=200))
            if verdict in ("fail", "adjusted"):
                impacts.append((x, aid, t["id"], verdict, performed, done_dates[1] if len(done_dates) > 1 else None))
        # the open task for the next due date
        start, end = work.window(next_due, sch, settings)
        at_vendor = STATUS.get(x["code"], ("", ""))[0] == "out_for_calibration"
        tx.x("INSERT INTO tasks (tenant_id, site_id, asset_id, schedule_id, kind, number, template_id, due_date, window_start,"
             " window_end, status, performer, vendor_id, assignee_roles, certificate_required, sent_at, source)"
             " VALUES (%s, %s, %s, %s, 'calibration', %s, %s, %s, %s, %s, %s, %s, %s, %s::text[], %s, %s, 'demo')",
             tid, sid, aid, s_id, number(next_due.year), tpl["id"] if tpl else None, next_due, start, end,
             "at_vendor" if at_vendor else "open", performer, vendors.get(vcode) if vcode else None,
             [] if performer == "external" else ["qa_calibration_officer"], performer == "external",
             dt.datetime.combine(today - dt.timedelta(days=4), dt.time(9, 0)) if at_vendor else None)
        n_tasks += 1
    for year, n in counters.items():
        tx.x("INSERT INTO counters (tenant_id, key, value) VALUES (%s, %s, %s)"
             " ON CONFLICT (tenant_id, key) DO UPDATE SET value = greatest(counters.value, EXCLUDED.value)", tid, f"CAL-{year}", n)
    for k, (x, aid, task_id, verdict, performed, prev) in enumerate(impacts, start=1):
        num = f"IA-{performed.year}-{k:03d}"
        approved = verdict == "adjusted"
        tx.x("INSERT INTO impact_assessments (tenant_id, site_id, asset_id, task_id, number, period_from, period_to, status, summary,"
             " scope, products, risk, actions, conclusion, approved_at, approved_by_name, source)"
             " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'demo')",
             tid, sid, aid, task_id, num, prev, performed, "approved" if approved else "draft",
             f"{x['code']} ({x['en']}) was found out of tolerance at the second calibration point during {num.replace('IA', 'calibration')}.",
             "Stability chamber SC-02 humidity control and the samples stored in it." if approved else
             "Wet chemistry lab R-202: pH measurements of raw materials, in-process and finished product tests.",
             "Stability samples stored at 40 °C / 75 % RH during the period; RH stayed within ±5 % per the independent data logger."
             if approved else "pH results reported with PH-01 since the last good calibration (QC LIMS query to be attached).",
             "low" if approved else "medium",
             "1. Compare with independent logger LOG-01.\n2. Confirm no excursion beyond ±5 % RH.\n3. Re-calibrate sensor (done)."
             if approved else "1. Keep PH-01 in quarantine.\n2. Retest retained samples with a qualified meter.\n3. Replace electrode and re-calibrate.",
             "No impact on stability data: the sensor drift stayed inside the protocol limits." if approved else None,
             dt.datetime.combine(performed + dt.timedelta(days=3), dt.time(11, 0)) if approved else None,
             "QA approver (demo)" if approved else None)
    tx.x("INSERT INTO counters (tenant_id, key, value) SELECT %s, 'IA-' || to_char(now(), 'YYYY'), %s"
         " ON CONFLICT (tenant_id, key) DO UPDATE SET value = greatest(counters.value, EXCLUDED.value)", tid, len(impacts))
    return {"schedules": n_sched, "tasks": n_tasks, "templates": len(TEMPLATES), "impact_assessments": len(impacts)}


def _back(d: dt.date, value: int, unit: str) -> dt.date:
    return work._sub_months(d, value * (12 if unit == "year" else 1)) if unit in ("month", "year") else \
        d - dt.timedelta(days=value * (7 if unit == "week" else 1))


import decimal as _decimal  # noqa: E402


def decimal_(v) -> _decimal.Decimal:
    return v if isinstance(v, _decimal.Decimal) else _decimal.Decimal(str(v))


# ================================================================== preventive maintenance demo
# (code, title, title_ar, interval (value, unit), minutes, items [(type, text, unit, low, high, page)], spare parts)
PM_TEMPLATES = [
    ("PM-TP", "Tablet press – monthly PM", "ماكينة كبس الأقراص – صيانة شهرية", (1, "month"), 180, [
        ("check", "Lubricate the turret, cam tracks and punch guides as per the lubrication chart", None, None, None, "6-4"),
        ("check", "Inspect upper and lower punches and dies for wear, chipping and sticking", None, None, None, "6-7"),
        ("check", "Check the feeder paddles and feeder seals; clean product build-up", None, None, None, "6-9"),
        ("reading", "Hydraulic overload system pressure", "bar", 80, 120, "6-12"),
        ("reading", "Main motor current at 40 rpm, no load", "A", 4, 9, "6-13"),
        ("check", "Test the guard door interlocks and the emergency stop", None, None, None, "2-3"),
        ("text", "Running hours counter", "h", None, None, "6-2"),
        ("photo", "Photo of the turret after cleaning", None, None, None, None)],
     [("Punch tip lubricant (food grade), 400 g", "LUB-FG-400", 1, "can"), ("Feeder seal kit", "P1200-FS-01", 1, "set")]),
    ("PM-GRAN", "Granulation equipment – monthly PM", "معدات التحبيب – صيانة شهرية", (1, "month"), 120, [
        ("check", "Inspect the impeller and chopper seals for leaks; check purge air", None, None, None, "5-2"),
        ("check", "Check drive belts for tension and wear", None, None, None, "5-4"),
        ("reading", "Impeller motor current at low speed", "A", 10, 30, "5-6"),
        ("check", "Inspect the filter bags / filter cartridges and their clamps", None, None, None, "5-9"),
        ("check", "Verify the emergency stop and the lid / bowl interlocks", None, None, None, "2-1"),
        ("text", "Remarks on the earthing (grounding) clamp and cable", None, None, None, "2-4")],
     [("Impeller shaft seal", "RMG-300-SS", 1, "pcs")]),
    ("PM-COAT", "Coater – monthly PM", "المغلف – صيانة شهرية", (1, "month"), 120, [
        ("check", "Clean the spray guns and check the nozzles and needles for wear", None, None, None, "7-3"),
        ("check", "Check the pan drive chain and the gear box oil level", None, None, None, "7-5"),
        ("reading", "Atomizing air pressure", "bar", 1.5, 3.0, "7-6"),
        ("check", "Inspect the exhaust filter and duct for coating build-up", None, None, None, "7-8"),
        ("check", "Test the door interlock and the emergency stop", None, None, None, "2-2")], []),
    ("PM-PACK", "Packaging line machine – monthly PM", "ماكينة التعبئة – صيانة شهرية", (1, "month"), 150, [
        ("check", "Check the forming and sealing heaters, thermocouples and cables", None, None, None, "8-2"),
        ("reading", "Sealing station temperature at set point", "°C", 175, 195, "8-3"),
        ("check", "Inspect the cutting knives and the perforation tool", None, None, None, "8-6"),
        ("check", "Lubricate the chains and the cams as per the chart", None, None, None, "8-9"),
        ("check", "Verify the vision system / missing tablet detection rejects a defective blister", None, None, None, "9-1"),
        ("check", "Test the guard interlocks and the emergency stops", None, None, None, "2-3")],
     [("Heater cartridge 230 V 400 W", "HC-230-400", 2, "pcs")]),
    ("PM-AHU", "Air handling unit – quarterly PM", "وحدة مناولة الهواء – صيانة ربع سنوية", (3, "month"), 240, [
        ("reading", "Pre-filter differential pressure", "Pa", 30, 150, "4-2"),
        ("reading", "Fine filter differential pressure", "Pa", 60, 250, "4-2"),
        ("check", "Check fan belt tension and alignment; replace if cracked", None, None, None, "4-5"),
        ("reading", "Supply fan motor current", "A", 8, 18, "4-6"),
        ("check", "Clean the cooling coil and the drain pan; check the drain trap", None, None, None, "4-8"),
        ("check", "Grease the fan bearings", None, None, None, "4-9"),
        ("check", "Check the dampers and actuators move freely", None, None, None, "4-11")],
     [("Pre-filter G4 592×592×48", "G4-592-48", 4, "pcs"), ("V-belt SPA 1250", "SPA-1250", 2, "pcs")]),
    ("PM-UTIL", "Utility system – quarterly PM", "نظام المرافق – صيانة ربع سنوية", (3, "month"), 180, [
        ("check", "Inspect for leaks, abnormal noise and vibration", None, None, None, "3-1"),
        ("reading", "Operating pressure", "bar", 6, 8.5, "3-3"),
        ("check", "Drain condensate traps and check automatic drains", None, None, None, "3-5"),
        ("check", "Check oil level / change oil if due by running hours", None, None, None, "3-7"),
        ("text", "Running hours counter", "h", None, None, "3-2"),
        ("check", "Test safety valves and pressure switches", None, None, None, "3-9")],
     [("Air / oil separator element", "SEP-0750", 1, "pcs"), ("Compressor oil 5 L", "OIL-C5", 1, "can")]),
    ("PM-EQUIP", "Process equipment – quarterly PM", "معدات التشغيل – صيانة ربع سنوية", (3, "month"), 60, [
        ("check", "Inspect power cable, plugs and earthing", None, None, None, None),
        ("check", "Clean fans, vents and filters", None, None, None, None),
        ("check", "Check door gaskets and seals", None, None, None, None),
        ("check", "Verify alarms and safety functions", None, None, None, None),
        ("text", "Observations", None, None, None, None)], []),
    ("PM-CHAMBER", "Stability chamber / cold store – quarterly PM", "غرفة الثبات / التبريد – صيانة ربع سنوية", (3, "month"), 90, [
        ("check", "Clean the condenser coil and check the refrigerant sight glass", None, None, None, "5-1"),
        ("check", "Check the humidifier water supply and drain (descale if needed)", None, None, None, "5-4"),
        ("check", "Inspect door gaskets and door heaters", None, None, None, "5-6"),
        ("check", "Test the high / low alarms and the remote alarm contact", None, None, None, "5-8"),
        ("reading", "Compressor suction pressure", "bar", 1.5, 4.5, "5-3")], []),
    ("PM-HPLC", "HPLC system – yearly preventive maintenance", "نظام HPLC – صيانة سنوية", (1, "year"), 240, [
        ("check", "Replace pump piston seals and check valves; check for leaks", None, None, None, "9-4"),
        ("check", "Replace the autosampler needle seat and rotor seal", None, None, None, "9-11"),
        ("reading", "UV lamp intensity / remaining lamp life", "%", 30, 100, "9-18"),
        ("reading", "Pump pressure ripple at 1 mL/min", "%", 0, 2, "9-6"),
        ("check", "Perform the instrument self-tests and save the report", None, None, None, "9-20")],
     [("Pump seal kit", "5063-6589", 1, "kit"), ("Needle seat", "G1367-87012", 1, "pcs"), ("Rotor seal", "0101-1416", 1, "pcs")]),
    ("PM-SAFETY", "Safety equipment – monthly inspection", "معدات السلامة – فحص شهري", (1, "month"), 15, [
        ("check", "In place, visible and access not blocked", None, None, None, None),
        ("check", "Seal, pin and pressure gauge in the green zone / water flows clear", None, None, None, None),
        ("check", "Inspection tag signed and dated", None, None, None, None)], []),
]

PM_FOR = {"TP": "PM-TP", "RMG": "PM-GRAN", "FBD": "PM-GRAN", "BLD": "PM-GRAN", "MIL": "PM-GRAN", "CP": "PM-COAT",
          "CF": "PM-PACK", "BL": "PM-PACK", "CT": "PM-PACK"}
PM_OVERDUE = {"BL-02": -9, "AHU-03": -21, "CA-01": -4}
PM_SOON = {"TP-01": 3, "AHU-01": 6, "FE-01": 2, "HPLC-01": 12}


def _pm_template(x: dict) -> str | None:
    if x["parent"]:
        return None
    if x["cls"] == "MACHINE":
        return PM_FOR.get(x["code"].split("-")[0], "PM-EQUIP")
    if x["cls"] == "AHU":
        return "PM-AHU"
    if x["cls"] == "UTILITY":
        return "PM-UTIL"
    if x["cls"] in ("CHAMBER", "FRIDGE"):
        return "PM-CHAMBER"
    if x["cls"] == "LABINST" and x["code"].startswith("HPLC"):
        return "PM-HPLC"
    if x["cls"] == "SAFETY" and x["code"].startswith(("FE", "EW")):
        return "PM-SAFETY"
    if x["cls"] == "EQUIP" and x["code"] in ("LAF-01", "AC-01", "BSC-01", "OVEN-01", "INC-01", "MD-01", "CW-01"):
        return "PM-EQUIP"
    return None


def load_pm(call: Call, sid: str, ids: dict[str, str], vendors: dict[str, str], today: dt.date) -> dict:
    tx, tid = call.tx, call.tenant_id
    settings = work.effective_settings(tx.val("SELECT work_settings FROM tenants WHERE id = %s", tid))
    templates: dict[str, dict] = {}
    for code, title, title_ar, (iv, iu), minutes, items, parts in PM_TEMPLATES:
        t = tx.val("INSERT INTO templates (tenant_id, kind, code, title, title_ar, method_ref, status, source, spare_parts,"
                   " suggested_interval_value, suggested_interval_unit, est_minutes)"
                   " VALUES (%s, 'pm', %s, %s, %s, %s, 'draft', 'demo', %s::jsonb, %s, %s, %s) RETURNING id",
                   tid, code, title, title_ar, f"SOP-ENG-{100 + len(templates):03d}",
                   json.dumps([{"name": n, "part_no": p, "qty": q, "unit": u, "page_ref": None, "notes": None} for n, p, q, u in parts]),
                   iv, iu, minutes)
        its = []
        for n, (typ, text, unit, lo, hi, page) in enumerate(items, start=1):
            iid = tx.val("INSERT INTO template_items (tenant_id, template_id, seq, item_type, parameter, unit, tol_mode, low_limit,"
                         " high_limit, required, page_ref) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                         tid, t, n, typ, text, unit, "limits" if typ == "reading" else None, lo, hi, typ != "photo", page)
            its.append({"id": iid, "seq": n, "type": typ, "text": text, "unit": unit, "lo": lo, "hi": hi})
        tx.x("UPDATE templates SET status = 'approved', approved_at = now(), approved_by_name = 'Engineering manager (demo)'"
             " WHERE id = %s", t)
        templates[code] = {"id": t, "items": its, "interval": (iv, iu), "parts": parts, "minutes": minutes}
    counters: dict[int, int] = {}

    def number(year: int) -> str:
        counters[year] = counters.get(year, 0) + 1
        return f"PM-{year}-{counters[year]:04d}"

    n_sched = n_tasks = 0
    for x in A:
        if STATUS.get(x["code"], ("in_service", None))[0] in ("draft", "retired"):
            continue
        tcode = _pm_template(x)
        if not tcode:
            continue
        tpl = templates[tcode]
        iv, iu = tpl["interval"]
        aid = ids[x["code"]]
        idays = work.interval_days(iv, iu)
        sch = {"kind": "pm", "interval_value": iv, "interval_unit": iu, "basis": "fixed", "tol_before_value": None,
               "tol_before_unit": None, "tol_after_value": None, "tol_after_unit": None}
        if x["code"] in PM_OVERDUE:
            nominal = today + dt.timedelta(days=PM_OVERDUE[x["code"]] - 8)
        elif x["code"] in PM_SOON:
            nominal = today + dt.timedelta(days=PM_SOON[x["code"]])
        else:
            nominal = today + dt.timedelta(days=int(5 + _h("pm" + x["code"]) * max(1, idays - 8)))
        performer = "external" if tcode == "PM-HPLC" else "internal"
        vendor = vendors.get("PMS") if performer == "external" else None
        roles = [] if performer == "external" else ["maintenance_technician"]
        s_id = tx.val(
            "INSERT INTO schedules (tenant_id, site_id, asset_id, kind, template_id, interval_value, interval_unit, basis,"
            " performer, vendor_id, assignee_roles, last_done, next_due)"
            " VALUES (%s, %s, %s, 'pm', %s, %s, %s, 'fixed', %s, %s, %s::text[], %s, %s) RETURNING id",
            tid, sid, aid, tpl["id"], iv, iu, performer, vendor, roles, _back(nominal, iv, iu), nominal)
        n_sched += 1
        # a year of history
        dues, d = [], _back(nominal, iv, iu)
        while d > today - dt.timedelta(days=370) and len(dues) < 12:
            dues.append(d)
            d = _back(d, iv, iu)
        for k, nd in enumerate(reversed(dues)):
            due = work.working_day(nd, settings, "pm")
            start, end = work.window(due, sch, settings)
            performed = min(today - dt.timedelta(days=1), due + dt.timedelta(days=int((_h(x["code"] + "p" + str(k)) - 0.5) * 4)))
            findings = _h(x["code"] + "f" + str(k)) > 0.93
            t = tx.val(
                "INSERT INTO tasks (tenant_id, site_id, asset_id, schedule_id, kind, number, template_id, due_date, window_start,"
                " window_end, status, performer, vendor_id, assignee_roles, accepted_at, accepted_by_name, performed_date,"
                " performed_by_name, verdict, remarks, man_hours, completed_at, completed_by_name, reviewed_at, reviewed_by_name, source)"
                " VALUES (%s, %s, %s, %s, 'pm', %s, %s, %s, %s, %s, 'completed', %s, %s, %s::text[], %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, 'demo') RETURNING id",
                tid, sid, aid, s_id, number(due.year), tpl["id"], due, start, end, performer, vendor, roles,
                dt.datetime.combine(performed, dt.time(8, 0)), "Maintenance technician (demo)", performed,
                "Service engineer (demo)" if performer == "external" else "Maintenance technician (demo)",
                "fail" if findings else "pass",
                "Finding raised; corrective work order to follow." if findings else None,
                round(tpl["minutes"] / 60 * (0.8 + _h(x["code"] + "m" + str(k)) * 0.5), 1),
                dt.datetime.combine(performed, dt.time(15, 0)), "Maintenance technician (demo)",
                dt.datetime.combine(performed, dt.time(16, 30)), "Engineering supervisor (demo)")
            n_tasks += 1
            bad_seq = 1 + int(_h(x["code"] + "b" + str(k)) * len(tpl["items"])) if findings else None
            for it in tpl["items"]:
                value = found = ok = None
                if it["type"] == "check":
                    value = "not_ok" if (findings and it["seq"] == bad_seq) else "ok"
                    ok = value == "ok"
                elif it["type"] == "reading":
                    span = it["hi"] - it["lo"]
                    found = round(it["lo"] + span * (0.3 + _h(x["code"] + str(it["seq"]) + str(k)) * 0.4), 2)
                    ok = True
                elif it["type"] == "text":
                    value = str(1200 + k * 160 + int(_h(x["code"]) * 400)) if it["unit"] == "h" else "No remarks"
                tx.x("INSERT INTO task_results (tenant_id, task_id, item_id, seq, item_type, parameter, unit, low_limit, high_limit,"
                     " as_found, value_text, ok_found, notes, required) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     tid, t, it["id"], it["seq"], it["type"], it["text"], it["unit"], it["lo"], it["hi"], found, value, ok,
                     "Wear found; part ordered." if value == "not_ok" else None, it["type"] != "photo")
            if tpl["parts"] and (k % 3 == 0 or iu == "year"):
                n_, p_, q_, u_ = tpl["parts"][0]
                tx.x("INSERT INTO task_parts (tenant_id, task_id, name, part_no, qty, unit) VALUES (%s, %s, %s, %s, %s, %s)",
                     tid, t, n_, p_, q_, u_)
        due = work.working_day(nominal, settings, "pm")
        start, end = work.window(due, sch, settings)
        tx.x("INSERT INTO tasks (tenant_id, site_id, asset_id, schedule_id, kind, number, template_id, due_date, window_start,"
             " window_end, status, performer, vendor_id, assignee_roles, source)"
             " VALUES (%s, %s, %s, %s, 'pm', %s, %s, %s, %s, %s, 'open', %s, %s, %s::text[], 'demo')",
             tid, sid, aid, s_id, number(due.year), tpl["id"], due, start, end, performer, vendor, roles)
        n_tasks += 1
    for year, n in counters.items():
        tx.x("INSERT INTO counters (tenant_id, key, value) VALUES (%s, %s, %s)"
             " ON CONFLICT (tenant_id, key) DO UPDATE SET value = greatest(counters.value, EXCLUDED.value)", tid, f"PM-{year}", n)
    return {"pm_schedules": n_sched, "pm_tasks": n_tasks, "pm_templates": len(PM_TEMPLATES)}


def load_plans(call: Call, sid: str, today: dt.date) -> int:
    """Demo plans: this year's plans approved; next year's calibration plan approved and its PM plan reviewed, waiting
    for QA, so the QA approval can be shown."""
    from .work import plan_content
    n = 0
    for kind in ("calibration", "pm"):
        for year in (today.year, today.year + 1):
            c = plan_content(call, sid, kind, year)
            status = "reviewed" if (kind == "pm" and year > today.year) else "approved"
            call.tx.x("INSERT INTO plans (tenant_id, site_id, kind, year, status, task_count, content_hash, reviewed_at,"
                      " reviewed_by_name, approved_at, approved_by_name, source)"
                      " VALUES (%s, %s, %s, %s, %s, %s, %s, now(), 'Engineering manager (demo)', %s, %s, 'demo')",
                      call.tenant_id, sid, kind, year, status, c["count"], c["hash"],
                      dt.datetime.now(dt.timezone.utc) if status == "approved" else None,
                      "QA approver (demo)" if status == "approved" else None)
            n += 1
    return n


DEMO_FORMS = [("QA027-F01", "Annual calibration schedule", "calibration_plan", "QA027-00/F01", "00"),
              ("EN002-F01", "Annual preventive maintenance schedule", "pm_plan", "EN002-01/F01", "01"),
              ("QA027-F02", "Calibration record", "calibration_record", "QA027-00/F02", "00"),
              ("EN002-F02", "Preventive maintenance record", "pm_record", "EN002-01/F02", "01"),
              ("EN003-F02", "Equipment history card", "history_card", "EN003-00/F02", "00"),
              ("QA027-L01", "Status label", "status_label", "QA027-00/L01", "00")]


def load_forms(call: Call) -> int:
    from .. import forms
    n = 0
    for code, title, source, fmt, rev in DEMO_FORMS:
        if call.tx.val("SELECT 1 FROM form_templates WHERE code = %s", code):
            continue
        call.tx.x("INSERT INTO form_templates (tenant_id, code, title, source, engine, layout, format_no, revision, is_default, status,"
                  " approved_at, approved_by_name, source_kind) VALUES (%s, %s, %s, %s, 'blocks', %s::jsonb, %s, %s, true, 'approved', now(),"
                  " 'QA approver (demo)', 'demo')", call.tenant_id, code, title, source, json.dumps(forms.default_layout(source)), fmt, rev)
        n += 1
    return n


# ================================================================== Phase 6: run-hours and the production plan
PRODUCTS = ["Paracetamol 500 mg tablets", "Amoxicillin 500 mg capsules", "Metformin 850 mg tablets", "Ibuprofen 400 mg tablets",
            "Omeprazole 20 mg capsules", "Vitamin C 1000 mg effervescent", "Diclofenac 50 mg tablets"]


def load_phase6(call: Call, sid: str, ids: dict[str, str], today: dt.date) -> dict:
    """Run-hours meters on the production machines (two with run-hours PM triggers) and a released production plan for
    the next eight weeks, as Complanning would send it."""
    from .. import hours
    tx, tid = call.tx, call.tenant_id
    machines = tx.q("SELECT a.id, a.code FROM assets a JOIN asset_classes c ON c.id = a.class_id WHERE a.site_id = %s::uuid"
                    " AND c.code = 'MACHINE' AND a.status IN ('in_service', 'restricted') AND a.parent_id IS NULL ORDER BY a.code", sid)
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    readings = windows = 0
    for i, m in enumerate(machines):
        rate = 12 + round(_h("rate" + m["code"]) * 8, 1)          # run-hours per day
        tx.x("UPDATE assets SET meter_unit = 'hours' WHERE id = %s", m["id"])
        total = 4000 + int(_h("meter" + m["code"]) * 20000)
        for k in range(18, -1, -1):                                # weekly readings for four months
            at = now - dt.timedelta(days=7 * k, hours=int(_h(m["code"] + str(k)) * 6))
            if k:
                total += rate * 7 * (0.85 + 0.3 * _h("w" + m["code"] + str(k)))
            tx.x("INSERT INTO meter_readings (tenant_id, asset_id, reading, read_at, source, entered_by_name) VALUES"
                 " (%s, %s, %s, %s, 'demo', 'Maintenance technician (demo)')", tid, m["id"], round(total, 1), at)
            readings += 1
        # campaigns for the next eight weeks, with short changeover gaps between them
        t = dt.datetime.combine(today, dt.time(6), dt.timezone.utc) - dt.timedelta(days=2)
        k = 0
        while t < dt.datetime.combine(today + dt.timedelta(days=56), dt.time(), dt.timezone.utc):
            length = dt.timedelta(days=2 + int(_h("c" + m["code"] + str(k)) * 4), hours=int(_h("h" + m["code"] + str(k)) * 12))
            prod = PRODUCTS[int(_h("p" + m["code"] + str(k)) * len(PRODUCTS))]
            tx.x("INSERT INTO production_windows (tenant_id, asset_id, starts_at, ends_at, label, ref, source) VALUES"
                 " (%s, %s, %s, %s, %s, %s, 'demo')", tid, m["id"], t, t + length, prod, f"CP-{today.year % 100}-{300 + windows}")
            windows += 1
            gap = dt.timedelta(hours=8 + int(_h("g" + m["code"] + str(k)) * 22))
            t = t + length + gap
            k += 1
    # run-hours triggers on the first two machines' PM: one already used up, one close
    trig = 0
    for m, frac in zip(machines[:2], (1.08, 0.82)):
        s = tx.one("SELECT id FROM schedules WHERE asset_id = %s AND kind = 'pm' AND active ORDER BY created_at LIMIT 1", m["id"])
        if not s:
            continue
        tot = hours.run_totals(tx, [m["id"]])[str(m["id"])]
        interval = 500
        tx.x("UPDATE schedules SET hours_interval = %s, hours_at_last = %s WHERE id = %s", interval,
             round(float(tot["total"]) - interval * frac, 1), s["id"])
        trig += 1
    st = work.effective_settings(tx.val("SELECT work_settings FROM tenants WHERE id = %s", tid))
    hours.check(tx, today, st)
    return {"meter_readings": readings, "production_windows": windows, "hours_triggers": trig}
