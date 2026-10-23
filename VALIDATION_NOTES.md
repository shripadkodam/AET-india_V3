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


## v0.3.1 parser fix (2026-10-23)

- Fixed DXF loading to write the uploaded bytes to a temporary file and call `ezdxf.readfile()`, which performs file-level DXF format and encoding detection.
- Root cause reproduced locally: `ezdxf.read(StringIO(decoded_text))` failed on the uploaded architectural DXF at a long group-code 310 binary-data tag, while `ezdxf.readfile(path)` successfully parsed it.
- Tested uploaded architectural DXF: parsed successfully; modelspace contains 1,815 entities; `$INSUNITS=4` (millimetres).
- Tested beam layout DXF: parsed successfully; modelspace contains 7 top-level entities; `$INSUNITS=4` (millimetres).
- These checks validate file parsing only. They do not validate quantities or drawing interpretation.
