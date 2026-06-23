# Stress Test Results

## Test Date: _____________________

## Test Configuration
- Frontend: 1 Nginx replica
- Backend: 4 Flask replicas  
- Network: Docker Swarm Overlay

---

## Test 1: Round-Robin Distribution

| Container ID | Requests Handled | Percentage |
|---|---|---|
| | | |
| | | |
| | | |
| | | |
| **Total** | **100** | **100%** |

**Expected**: ~25% per container  
**Actual**: _____________________  
**Verdict**: ☐ PASS / ☐ FAIL

---

## Test 2: Concurrent Load

- Requests sent: 200
- Completion time: _____ ms
- HTTP errors: _____
- **Verdict**: ☐ PASS / ☐ FAIL

---

## Test 3: CPU-Intensive Distribution

| Request # | Container ID | Computation Time |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| ... | | |

---

## Docker Stats During Test

```
(Paste docker stats output here)
```

## Conclusion

_____________________
