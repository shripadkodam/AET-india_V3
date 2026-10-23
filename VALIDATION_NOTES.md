# AET India v0.3 — Initial DXF Inspection Notes

These observations were made by parsing the supplied DXF files with ezdxf 1.4.4. They are not a certification of drawing accuracy.

| Check | Architectural DXF | Beam-layout DXF |
|---|---:|---:|
| `$INSUNITS` | 4 = millimetres | 4 = millimetres |
| Model-space entities | 1,815 | 7 |
| Dimension entities found in block definitions | 111 | 148 |
| Layout names | KR_01–KR_06, KR_MP_01–KR_MP_03, Model | Layout1, Model |
| Reference 35,038 mm | Stored dimension appears 3 times in blocks | Not used as a beam validation reference |

The architectural dimension values match the entered 35,038 mm reference exactly. Because this comparison uses the DXF dimension entity's stored measurement, it should be followed by a separate geometric distance check against the intended grid/element and a second independent dimension in another direction.

No BOQ quantities were approved or inferred from these diagnostics.
