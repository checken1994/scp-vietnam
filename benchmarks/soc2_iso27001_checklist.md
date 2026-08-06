# SOC 2 / ISO 27001 Checklist cho SCP

## SOC 2
- [x] Access control (verify_admin)
- [x] Audit logging (autofix_audit.jsonl)
- [ ] Encryption at rest (need SQLCipher)
- [ ] HTTPS (need reverse proxy)

## ISO 27001
- [x] Security policy (constitution.py)
- [x] Access control (verify_admin)
- [x] Incident management (threat_detector + counter_response)
- [ ] Key management (token rotation)
