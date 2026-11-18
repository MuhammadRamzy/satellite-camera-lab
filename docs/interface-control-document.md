# Mission interface control document — v1 draft

Implemented subset: interfaces/space_packet.py and payload_gateway. Phase 4
verifies live sockets, dispatch, persistent duplicate journal, correlated ACKs,
status and restart behavior. File transport runtime acceptance is the next gate.

Reference: [CCSDS Space Packet Protocol, 133.0-B-2 with corrections](https://ccsds.org/Pubs/133x0b2e2.pdf).
This implements an unsegmented mission subset of the primary header, not a
claim of complete CCSDS protocol conformance or interoperability certification.
No custom format is called CFDP.

All integers use network byte order, unsigned, with exact widths. SPP primary
header has 6 bytes: packet identification uint16 (version bits=0, command/type
bit, secondary-header flag=1, APID 11 bits); sequence control uint16
(flags=3, sequence 14 bits); packet data length uint16 (data bytes minus one).
Total length is length-field + 7. Mission packet limit is 4096 bytes.
Packets with other versions, segmentation, missing secondary header, idle APID,
inconsistent length or oversized declared data are rejected.

Mission secondary header (14 bytes): ICD version uint8=1, opcode uint8,
command ID uint32, UTC microseconds since Unix epoch uint64. This is a custom
mission header, not a standardized CCSDS time code. Command ID must be nonzero.
The application body follows immediately; trailing bytes are forbidden for TC.

| APID | Type | Purpose |
|---|---|---|
| 0x100 | TC (type=1) | Payload commands |
| 0x101 | TM (type=0) | Command ACK/error, planned |
| 0x102 | TM | 1 Hz housekeeping, planned |
| 0x103 | TM | File-ready/fault/mode events, planned |

| Opcode | Command | Body |
|---|---|---|
| 1 | PL_PING | Empty |
| 2 | PL_GET_STATUS | Empty |
| 3 | PL_SET_MODE | uint8: 1 STANDBY, 2 READY, 3 DOWNLINK, 4 SAFE |
| 4 | PL_CAPTURE | Empty, uses accepted configuration |
| 5 | PL_CAPTURE_BURST | count uint16 [1,100], interval_ms uint32 [100,60000] |
| 6 | PL_SET_CAMERA_CONFIG | width uint16 [16,1920], height uint16 [16,1080], fps uint8 [1,10], quality uint8 [1,100], exposure_us uint32 [1,1000000], noise uint8 [0,100], source uint8 (0 synthetic, 1 directory, 2 V4L2) |
| 7 | PL_LIST_FILES | offset uint32, limit uint16 [1,32] |
| 8 | PL_REQUEST_FILE | 16-byte UUID |
| 9 | PL_DELETE_FILE | 16-byte UUID; dispatch must guard ground confirmation |
| 10 | PL_ABORT_CAPTURE | Empty |
| 11 | PL_SAFE | Empty |
| 12 | PL_CONFIRM_FILE | 16-byte UUID + 32-byte ground SHA-256 |
| 13 | PL_SPACECRAFT_CONTROL | uint8 mode (0 STANDBY, 1 OPERATIONAL, 2 SAFE, 3 COMMUNICATIONS), uint8 permission (0/1, only OPERATIONAL may permit) |
| 14 | PL_RECOVER | Empty; explicit recovery to STANDBY |

Sequence tracking is per APID within a TCP session, wraps 16383→0 and rejects
gaps/replays/out-of-order. New sessions explicitly create new tracker objects.
TC stream parsing uses the primary header for length. Close on malformed input;
there is no sync marker to search for. A future endpoint must impose read
deadlines and maximum connection/read sizes before feeding this decoder.

Implemented duplicate semantics before dispatch: persistent command ID and
request fingerprint, completed response replay, conflicting reuse rejected,
in-progress result reported without repeating exposure. A crash between physical
side effect and journal completion must produce IN_DOUBT and reconcile by image
ID; exactly-once side effects cannot be inferred from TCP delivery. Command IDs
must survive bus restart; sequence count alone is not duplicate protection.

ACK APID 0x101 echoes command ID/opcode and has an independent session sequence.
Its body is bounded UTF-8 JSON: status COMPLETED (synchronous result), ACCEPTED
(capture/action accepted), ERROR or IN_DOUBT; reason is a bounded diagnostic.
Capture ACK includes capture_id; burst ACK includes job (command ID string).
GET_STATUS/PING carry fresh state, health, counts, resource snapshot and up to
eight burst job summaries with durable-completion feedback. The bus requests
GET_STATUS at 1 Hz; this version has no unsolicited APID 0x102/0x103 streams.
LIST returns compact file entries and next_offset; pagination uses the actual
returned count to stay within the 4096-byte packet cap. REQUEST returns the
UUID, size, SHA-256 and separate file port. CONFIRM/DELETE are serialized by
the storage owner. A capture ACK is never a storage or ground-delivery receipt.
Configuration is atomic and requires STANDBY. Only source=0 is implemented;
directory and V4L2 selections return ERROR. FPS describes the intended burst
interval; it does not start continuous acquisition.

Bulk transport design is separate from SPP: transfer ID, file UUID, total length,
SHA-256 manifest; bounded chunks with sequence/offset, CRC32, ACK and retransmit;
durable contiguous receiver offset for restart; final SHA-256 before rename and
delivery receipt. This experimental transport is implemented and host-tested;
live file/radio acceptance follows Phase 4. It is not CFDP.
