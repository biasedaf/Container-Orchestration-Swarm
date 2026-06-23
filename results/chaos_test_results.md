# Chaos Test Results

## Test Date: _____________________

## Test Configuration
- Service: cc_research_backend
- Desired replicas: 4
- Restart policy: any (unlimited)

---

## Before Kill

| Container ID | Name | Status |
|---|---|---|
| | | |
| | | |
| | | |
| | | |

Running replicas: **4/4** ✅

---

## Kill Event

- **Container killed**: _____________________
- **Kill time**: _____________________
- **Kill command**: `docker rm -f <container_id>`

---

## Self-Healing Monitoring

| Time (seconds) | Running Replicas | Notes |
|---|---|---|
| 0 (kill) | 3/4 | Container killed |
| 1 | /4 | |
| 2 | /4 | |
| 3 | /4 | |
| ... | /4 | |
| __ | 4/4 | ✅ Fully recovered |

**Recovery Time**: _____ seconds

---

## After Recovery

| Container ID | Name | Status |
|---|---|---|
| | | |
| | | |
| | | |
| | | |

Running replicas: **4/4** ✅

---

## Application Availability During Chaos

- HTTP Status during kill: _____
- Response received: ☐ Yes / ☐ No
- **Availability maintained**: ☐ 100% / ☐ Partial downtime

---

## Conclusion

**Docker Swarm Self-Healing**: ☐ VERIFIED / ☐ NOT VERIFIED  
**Recovery Time**: _____ seconds  
**Application Downtime**: _____ seconds
