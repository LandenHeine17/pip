# Pip 2.0 — Requirements Traceability (PROPOSAL)

## 1. Ziggletron feature → Pip requirements → acceptance criteria

Decisions are from [01](01-FEATURE-DECISION-MATRIX.md). "Spec" points to the defining section. Every FEAT-001…089 appears once.

| FEAT | Decision | Pip requirements | Spec | Acceptance |
|---|---|---|---|---|
| 001 | REDESIGN | PIP-NEW-001, 002, 013 | 04 §2 | AC-SP-01, 02, 09 |
| 002 | REDESIGN | PIP-NEW-008, 010, 013 | 04 §3, §5 | AC-SP-06, 07 |
| 003 | REDESIGN | PIP-NEW-001 | 04 §2.1 | AC-SP-03, 04 |
| 004 | IMPROVE | PIP-NEW-018 | 03 §2 | AC-CV-05 |
| 005 | REDESIGN | PIP-NEW-020 | 03 §2.3 | — (M2 test to be added) |
| 006 | KEEP | — | — | 12 §2 budget |
| 007 | IMPROVE | PIP-NEW-014 (own-voice) | 04 §2.2 | AC-CV-01 |
| 008 | IMPROVE | PIP-NEW-009 | 04 §4 | AC-SP-08 |
| 009 | IMPROVE | PIP-NEW-012 | 03 §2.1 | AC-CV-07 |
| 010 | IMPROVE | PIP-NEW-027 | 03 §4 | AC-CV-09 |
| 011 | REDESIGN | PIP-NEW-024, 025 | 03 §4.2–4.3 | AC-CV-08 |
| 012 | IMPROVE | PIP-NEW-026 | 03 §4.2 | — |
| 013 | IMPROVE | PIP-NEW-025 | 03 §4.3 | AC-CV-08 |
| 014 | REPLACE | PIP-NEW-027, 054 | 11 §2 | AC-CV-09, AC-SEC-01 |
| 015 | IMPROVE | PIP-NEW-039 | 12 §1–2 | AC-REL-03 |
| 016 | IMPROVE | PIP-NEW-014, 015 | 04 Part B | AC-TTS-01, 02 |
| 017 | REDESIGN | PIP-NEW-006, 016, 021 | 03 §3 | AC-CV-01, 02, 03 |
| 018 | KEEP | — | — | — |
| 019 | REDESIGN | PIP-NEW-003, 004, 018, 019 | 03 §1–2 | AC-CV-04, 05, 06 |
| 020 | KEEP | PIP-NEW-004 | 03 §1.2 | AC-CV-04 |
| 021 | IMPROVE | — | 03 §2.4 | AC-CV-07 |
| 022 | IMPROVE | PIP-NEW-053 | 03 §5 | AC-UI-01 |
| 023 | IMPROVE | PIP-NEW-028 | 06 §4 | AC-MEM-04 |
| 024 | KEEP | — | 11 §7 | — |
| 025 | REDESIGN | PIP-NEW-039 | 12 §1 | AC-SCH-04, AC-SH-01 |
| 026 | IMPROVE | PIP-NEW-044, 081 | 12 §3 | AC-SCH-01, AC-REL-04 |
| 027 | IMPROVE | PIP-NEW-054 | 11 §2 | AC-SEC-01 |
| 028 | REDESIGN | PIP-NEW-028, 029, 031 | 06 §2–3 | AC-MEM-01, 02 |
| 029 | IMPROVE | PIP-NEW-039 | 06 §3 | AC-SCH-04 (pattern) |
| 030 | IMPROVE | PIP-NEW-028 | 06 §4 | AC-MEM-04 |
| 031 | MERGE | PIP-NEW-029 | 06 §3.1 | — |
| 032 | REPLACE | PIP-NEW-030, 034 | 06 §5 | AC-MEM-07 |
| 033 | IMPROVE | PIP-NEW-044 | 12 §3 | AC-SCH-01 |
| 034 | IMPROVE | PIP-NEW-046 | 08 §3 | AC-UI-01 |
| 035 | IMPROVE | — | — | — |
| 036 | IMPROVE | PIP-NEW-046, 049 | 08 §3 | — |
| 037 | IMPROVE | PIP-NEW-051 | 03 §3 | AC-CV-07 |
| 038 | IMPROVE | PIP-NEW-051 | 03 §3 | — |
| 039 | KEEP | PIP-NEW-045 | 12 §3 | AC-SCH-01 |
| 040 | KEEP | — | — | — |
| 041 | KEEP | — | — | AC-SCH-03 |
| 042 | IMPROVE | PIP-NEW-039 | 12 §1 | AC-SCH-04 |
| 043 | IMPROVE | PIP-NEW-058 | 11 §6 | AC-SEC-03 |
| 044 | IMPROVE | PIP-NEW-039, 044 | 12 | AC-SCH-01 |
| 045 | REDESIGN | PIP-NEW-005 | 03 §1.2 | AC-CV-07; D-14 |
| 046 | IMPROVE | PIP-NEW-054, 084 | 11 | AC-SEC-01, AC-CV-12 |
| 047 | KEEP | — | — | — |
| 048 | KEEP | — | — | — |
| 049 | IMPROVE | PIP-NEW-036 | 07 §1.2 | AC-SH-04 |
| 050 | KEEP | PIP-NEW-042 | 07 §6 | — |
| 051 | MERGE | PIP-NEW-079 | 12 §4 | AC-SK-05 |
| 052 | IMPROVE | PIP-NEW-062 | 08 §4 | — |
| 053 | KEEP | — | 08 §5 | — |
| 054 | KEEP | — | — | — |
| 055 | IMPROVE | PIP-NEW-060 | 08 §5 | AC-UI-03 |
| 056 | REDESIGN | PIP-NEW-061 | 08 §6 | AC-UI-04, 05 |
| 057 | REDESIGN | PIP-NEW-061 | 08 §6 | AC-UI-04 |
| 058 | IMPROVE | PIP-NEW-060 | 08 §5 | AC-UI-03 |
| 059 | KEEP | — | 08 §7 | — |
| 060 | IMPROVE | PIP-NEW-063 | 08 §7 | D-21 |
| 061 | IMPROVE | PIP-NEW-063 | 08 §7 | — |
| 062 | IMPROVE | PIP-NEW-056 | 11 §4 | AC-UI-02 |
| 063 | KEEP | — | — | — |
| 064 | IMPROVE | PIP-NEW-042 | 07 §6 | — |
| 065 | MERGE | PIP-NEW-079 | 12 §4 | — |
| 066 | REDESIGN | PIP-NEW-052, 054 | 08 §1 | AC-SEC-01 |
| 067 | REDESIGN | PIP-NEW-056 | 11 §4 | AC-UI-02 |
| 068 | REDESIGN | PIP-NEW-056 | 11 §4 | AC-SEC-01 |
| 069 | REDESIGN | PIP-NEW-059 | 11 §5 | AC-SEC-03 |
| 070 | IMPROVE | PIP-NEW-010 | 04 §5 | — |
| 071 | REPLACE | PIP-NEW-054 | 11 §3 | AC-SEC-01 |
| 072 | IMPROVE | PIP-NEW-055 | 11 §3 | AC-SEC-02 |
| 073 | REDESIGN | PIP-NEW-069, 070, 071, 074 | 10 §2–4, §7 | AC-SM-01, 02, 05 |
| 074 | REPLACE | PIP-NEW-072 | 10 §5 | AC-SM-03, 04 |
| 075 | REPLACE | PIP-NEW-073, 075 | 10 §6, §8 | AC-SM-06 |
| 076 | IMPROVE | PIP-NEW-076 | 10 §9 | AC-SM-07 |
| 077 | REDESIGN | PIP-NEW-072 | 10 §11 | AC-SM-03 |
| 078 | IMPROVE | PIP-NEW-075 | 10 §8 | — |
| 079 | REDESIGN | — (plugin permissions) | 11 §7 | AC-SEC-05 |
| 080 | IMPROVE | PIP-NEW-053 | 08 §2 | AC-UI-01 |
| 081 | REPLACE | PIP-NEW-058 | 11 §6 | AC-SEC-03, 04 |
| 082 | KEEP | PIP-NEW-079 | 12 §4 | — |
| 083 | IMPROVE | PIP-NEW-079 | 12 §4 | AC-SK-05 |
| 084 | KEEP | — | 12 §5 | AC-REL-01 |
| 085 | IMPROVE | PIP-NEW-081 | 12 §3 | AC-REL-03, 04 |
| 086 | IMPROVE | PIP-NEW-080 | 12 §5 | AC-SM-06 |
| 087 | IMPROVE | PIP-NEW-079 | 12 §4 | AC-SK-05 |
| 088 | REDESIGN | PIP-NEW-090 | 07 §9 | D-20 |
| 089 | REPLACE | PIP-NEW-064–067 | 09 | AC-SK-01–04, 06 |

## 2. New requirement → origin

Each new requirement traces to a discovery finding (KNOWN-PROBLEMS IDs) or a brief part.

| Requirement | Origin |
|---|---|
| PIP-NEW-001–005 | V-01, V-02, R-01; brief Parts 3, 4.1; D-13 (opt-out) |
| PIP-NEW-006–013 | R-01, R-04, U-01, U-02; Part 3.2–3.5 |
| PIP-NEW-014–017 | Part 3.6 |
| PIP-NEW-018–023 | V-02, FEAT-005 issue; Part 4.2–4.3 |
| PIP-NEW-024–027 | V-04, P-07, S-04, S-06; Part 4.4 |
| PIP-NEW-028–035 | V-03, V-05, V-08, U-09; Part 5 |
| PIP-NEW-036–043 | Part 6 (6.1–6.3) |
| PIP-NEW-044–051 | R-03, U-02, P-05, U-09; Part 7 |
| PIP-NEW-052–059 | S-01, S-02, S-08, S-09, V-05, V-07, U-07; Parts 8, 8.1, 13 |
| PIP-NEW-060–063 | P-09, U-05, U-08; Part 9 |
| PIP-NEW-064–068 | FEAT-089; Part 10 |
| PIP-NEW-069–077 | S-03, S-04, V-06, U-11, P-02; Part 11 |
| PIP-NEW-078–082 | R-01, R-02, R-03, P-01; Part 12 |
| PIP-NEW-083 | Part 13.1; owner decision D-07 |
| PIP-NEW-084–089 | Adopted ideas (owner decision D-24) |
| PIP-NEW-090 | FEAT-088; owner decision D-20 |

## 3. Known-problem coverage

| Problem | Addressed by |
|---|---|
| P-01 | One session per request; AC-REL-03 |
| P-02 | Sandbox cleanup; AC-SM-04 |
| P-03 | PIP-NEW-039 |
| P-04 | FEAT-027 improvement |
| P-05 | PIP-NEW-046 |
| P-06 | FEAT-035 improvement; PIP-NEW-065 (discrepancy reporting) |
| P-07 | PIP-NEW-026 |
| P-08 | FEAT-064 single source |
| P-09 | PIP-NEW-061 |
| P-10 | FEAT-060 review |
| P-11 | Dead UI path removed in the redesign |
| P-12 | PIP-NEW-039 |
| S-01–S-09 | 11 (all sections) |
| V-01–V-08 | 03 §1, 06, 11 |
| R-01–R-08 | 12 |
| U-01–U-11 | 03, 08, 10 §11 |
