# Handover: erpocr_integration architect, 2026-10-28 (PC reboot)

Repo is clean at `b13353e` = origin/master. Tag v1.14.1 is pushed. Nothing is half-done on any live or test site.

## Waiting on others
- **Deploys** (DEPLOY-LIST-2026-10-27 rev4):
  - v1.14.1 on Star Pops: not landed yet.
  - Cactus: HELD at 1.14.0 until Willie sorts its "OCR Invoices" label (card ocr-cactus-label-before-1141).
  - erp-test: should pull version-16 `38e45a2`. Then run the v16 Sidebar check that is still owed.
  - Empire Vending: deploys on Willie's go, only after erp-test proves the build.
- **Jev trial:** ENABLED on Star Pops by Willie today at 14:59 (jev-1.14.0, budget 5, timeout 20).
  - The coordinator watches the first OCR Import after 14:59:54 and reports its `jev_status`. Expect Done.
  - Score the trial around 2026-12-09.
- **Willie's desk cards:**
  - ocr-second-approver;
  - ocr-danell-backlog-1027;
  - ocr-cactus-label-before-1141.
- **iso22000-fsms (policy seat):** SP-SOP-010 v2.4 is live and prescribes the OCR Delivery Note → Create Purchase Receipt flow. Any change to the DN flow must be told to that seat.

## Next release TODO (no release on its own)
- Move the Jev default model from `jev-1.13.0` to `jev-1.14.0`. Places: `tasks/jev_shadow.py`, the OCR Settings default, `docs/architecture.md`.
- Register Q17: record that the trial was enabled 2026-10-28.
