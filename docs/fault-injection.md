# Fault injection status

Library checks plus runtime invalid-camera-configuration and graceful payload-restart checks have executed in Space ROS. The full fault campaign is still open. The latest measured pipeline restart-ready time is in phase2-result.json; unit-test duration is not recovery time. `—` means not measured.

| ID | Initial state | Fault | Expected | Observed | Recovery action | Time | Result |
|---|---|---|---|---|---|---|---|
| F01 | Camera configured | Source unavailable | Reject capture; report health fault | Not run | Replace source, reconfigure | — | NOT RUN |
| F02 | Codec only | Invalid burst count/interval | Reject command | ProtocolError | Correct request | — | PASS, library only |
| F02a | Camera ready | Quality zero | Reject acquisition without a file | Service rejected; six expected captures only | Restore quality 75 | — | PASS, runtime |
| F03 | Capturing | Camera process crash | Bus/payload fault; bounded restart | Not run | Supervisor restart | — | NOT RUN |
| F04 | Connected | Gateway disconnect | Timeout/reconnect without repeat exposure | Not run | Reconnect/journal lookup | — | NOT RUN |
| F05 | Healthy bus | Missed heartbeat | Disable imaging, command SAFE | Not run | Restore health, explicit recovery | — | NOT RUN |
| F06 | Empty catalogue | Application quota below image size | ENOSPC, no committed capture | errno 28, no row | Free confirmed files/increase quota | — | PASS, quota only |
| F07 | Complete file | Overwrite file bytes | Retain and flag CORRUPT | Flagged after reopen | Quarantine/reacquire | — | PASS, library only |
| F08 | WRITING intent | Exception after fsync/rename | Recover complete valid file | COMPLETE and original bytes | Reopen catalogue | — | PASS, exception hook |
| F09 | Command journal | Duplicate command ID | Replay response; no second capture | Not run | Persistent lookup | — | NOT RUN |
| F10 | Packet decoder | Bad header/length/version | Reject | ProtocolError | Close connection (not yet implemented) | — | PASS, decoder only |
| F11 | Six complete captures | Stop/restart all three payload processes | Preserve files/catalogue | Six independent decodes and SHA-256 matches after restart | Relaunch same data directory | phase2-result.json restart_ready_ms | PASS, graceful runtime restart |
| F12 | Downlink active | Interrupt pass | Retain queue; resume next pass | Not run | Next window/resume | — | NOT RUN |
| F13 | Active pass | 1/5/10% loss | Retry under rate cap | Not run | Retransmit | — | NOT RUN |
| F14 | Awaiting receipt | Wrong ground SHA-256 | Block delivery confirmation/deletion | Rejected in catalogue test | Retransmit and reverify | — | PASS, library only |

Future tests must include real process termination, actual filesystem write
failure in an isolated quota-limited environment, checksum corruption in transit,
and independent receiver verification. Do not fill the PC's filesystem.
