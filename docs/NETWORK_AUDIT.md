# Network audit

Run on 2026-11-03 09:52 by `scripts/network_audit.py` (memory for 90 s on the real desktop, isolated data folder, then one question).

Recorded during the run: {'window': 8, 'frame': 34, 'text': 13, 'click': 10, 'field': 2, 'transcript': 9}.

| Phase | Kind | Destination | Opened by | Count | Leaves the PC |
|---|---|---|---|---|---|
| memory | connect | `127.0.0.1:57728` | `edith/brain/local.py` | 27 | no |
| memory | dns | `127.0.0.1` | `edith/brain/local.py` | 27 | no |
| question (cloud) | connect | `2a03:2880:f043:11:face:b00c:0:2:443` | `httpcore2` | 1 | **yes** |
| question (cloud) | dns | `api.meta.ai` | `httpcore2` | 1 | **yes** |
| question (no connection) | connect | `127.0.0.1:57728` | `edith/brain/local.py` | 1 | no |
| question (no connection) | connect | `127.0.0.1:9` | `httpcore2` | 2 | no |
| question (no connection) | dns | `127.0.0.1` | `edith/brain/local.py` | 1 | no |
| question (no connection) | dns | `127.0.0.1` | `httpcore2` | 2 | no |

llama-server (local model) TCP peers seen by netstat: 127.0.0.1:50377, 127.0.0.1:57695, 127.0.0.1:61912.
Server peers outside this PC: none.

## Result

- While recording (no question asked): **0 connections left the PC**.
- The question with the cloud reachable was answered by **cloud**; what was sent: *Sent: 13 screen texts, 9 transcript lines · $0.0055*.
- The same question with the cloud unreachable was answered by **local** (*Answered locally · no connection*).
