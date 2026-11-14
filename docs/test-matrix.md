# Test matrix

Sixteen scenarios the brief asked LocalDrop to survive, and what was actually
done about each one. Nothing here is a claim of "should work": the
**Evidence** column names the test or the session that produced the result, and
the **How** column says whether it was automated, driven in a real browser,
emulated, or done once by hand during development.

Confidence levels, worst to best:

| Label | Meaning |
| --- | --- |
| not tested | Nobody ran it. Do not rely on it. |
| manual once | Typed or clicked during development, result noted here, not repeatable from a clean checkout. |
| emulated | A script impersonated the device (old Safari User-Agent, LAN address, chunk sizes). Proves the *server* and the *protocol*, not the browser engine. |
| browser-driven | The real page was loaded and operated in a rendering browser at the stated viewport. |
| automated | `python -m unittest discover -s tests -q` proves it, on every run, on any machine. |

**Suite status at the time of writing: 128 tests, OK** (Python 3.12, Windows 11).

---

| # | Scenario | How | Evidence | Result |
| --- | --- | --- | --- | --- |
| 1 | Fresh pairing, then a small file from the PC to an iPad | automated + browser-driven + emulated | `PairingTest.test_remote_device_pairs_with_pin_and_gets_remote_role`, `DeviceToDeviceTest.test_offer_gates_the_download_until_the_receiver_accepts`; the page was driven in the app browser at 395 px and on the desktop host view; `.scratch/fake_ipad.py` paired from `192.168.1.10` with an iOS 9 UA | Offer appears on the receiving device, download refused until it accepts. |
| 2 | Large multi-chunk file (3 MB at 2 MiB chunks; 200 MB driven in the browser) | automated + browser-driven | `LargeTransferTest.test_multi_chunk_upload_and_ranged_download`, `test_state_reports_progress_while_uploading`; a real 200 MB `File` sent through the page's own Uploader while `.row-speed` was sampled mid-upload | SHA-256 of the received file equals the source; the sender row showed a live ↑ figure (~44 MB/s on loopback) with a time-left estimate and a flowing bar, then hid the figure and read *Delivered* once complete. |
| 3 | iPad loses Wi-Fi during an upload | automated | `InterruptionTest.test_connection_drop_mid_upload_keeps_verified_bytes`, `test_half_sent_chunk_is_resynced_not_corrupted`, `RecoveryTest.test_stalled_upload_is_marked_interrupted` | The socket dies mid-chunk; the server keeps only the bytes it can prove, reports that offset, and the transfer becomes *Interrupted* rather than corrupt. |
| 4 | Safari reloads the tab mid-upload and drops the `File` object | automated + browser-driven | `ControlTest.test_rebind_needs_the_identical_file`; `reselect()`/`attach()` exercised in the browser | Re-picking the same file continues from the confirmed offset. Re-picking a different file (or the same name at a different size) is refused with 409 and the row stays interrupted. |
| 5 | LocalDrop itself stops and restarts mid-transfer | automated | `RestartTest.test_paused_transfer_continues_after_the_server_restarts`, `RecoveryTest.test_transfer_survives_a_server_restart` | `reconcile()` compares each partial file with the database and adopts the real byte count; the queue and the device list are still there. |
| 6 | Device sleeps, then returns | automated | `RecoveryTest.test_silent_sender_becomes_interrupted_not_lost`, presence in `PairingTest` | After `device_timeout_s` of silence the device shows *Offline* and its transfers *Interrupted*; its session token still works, so it re-joins without re-pairing and can resume. |
| 7 | A chunk arrives twice, or out of order | automated | `UploadTest.test_duplicate_chunk_is_idempotent`, `test_out_of_order_chunk_is_rejected_with_the_expected_offset`, `test_server_owns_the_offset_after_an_interrupted_chunk` | A duplicate is acknowledged without a rewrite (`"duplicate": true`); an out-of-order chunk gets 409 plus the offset the server actually holds. |
| 8 | Truncated, oversized or corrupt data | automated | `UploadTest.test_finalize_before_all_bytes_keeps_the_partial`, `test_chunk_beyond_declared_size_is_refused`, `TransportTest.test_oversized_chunk_body_is_refused`, `test_empty_and_oversized_files_are_refused_at_creation` | Finalize refuses until every byte is present and the file on disk matches the declared size; the partial is kept for a retry, never renamed into the inbox. |
| 9 | Two devices transferring at the same time | automated | `LargeTransferTest.test_concurrent_uploads_finish_with_correct_bytes`, `DeviceToDeviceTest.test_a_device_cannot_write_into_another_devices_upload`, `test_a_device_cannot_read_another_devices_file`, `test_remote_device_only_sees_its_own_transfers` | Both files land byte-exact under per-transfer locks; neither device can see or touch the other's transfer. |
| 10 | Download to the iPad stops halfway | automated | `DownloadTest.*` (open-ended, bounded, suffix and unsatisfiable ranges, `HEAD`, partial progress), `InterruptionTest.test_download_abandoned_halfway_is_retried_from_the_start` | `206` + `Content-Range` + `ETag` + `Accept-Ranges: bytes` behave as Safari's download manager needs. **iOS owns that download; LocalDrop can only make resuming possible, never force it** — see the README. |
| 11 | Wrong pairing code, and a code rotated mid-session | automated + browser-driven | `PairingTest.test_wrong_pin_is_refused`, `test_pin_rotates_and_old_code_stops_working`, `test_pairing_throttles_brute_force`; rotation clicked in the settings sheet | Bad codes are refused (and 8 per minute per address gets a 429); after a rotation the old code stops working while already-paired devices keep their sessions. The settings sheet now shows and updates the code. |
| 12 | Something that is not on the LAN, or not paired | automated | `PairingTest.test_only_local_network_addresses_are_served`, `test_unpaired_session_is_rejected`, `test_admin_endpoints_need_the_host_role`, `TransportTest.test_security_headers_and_server_signature` | A public address gets 403 before routing; an unpaired session gets 401 with `needs_pairing`; `/api/settings`, `/api/pin/rotate`, `/api/history/clear`, `/api/server/stop` need host role **and** a loopback peer. |
| 13 | Hostile or colliding filenames | automated | `UploadTest.test_filenames_are_sanitised_and_never_overwrite`, `test_html_and_executables_are_downgraded` | `..\..\Windows\...`, reserved names (`CON`, `CLOCK$`), control characters and leading dots are neutralised; `report.pdf` twice becomes `report (2).pdf`; the second is never touched. |
| 14 | Cancel, delete, forget, and doing it twice | automated | `ControlTest.test_cancel_removes_the_partial_but_keeps_the_row`, `test_delete_removes_the_stored_file`, `test_chunks_are_refused_once_finished`, `PairingTest.test_unknown_path_is_404`; forget refuses unfinished transfers | Cancel deletes the partial and keeps the history row; late chunks are refused 409; forgetting a device with an open transfer is refused until it is canceled. |
| 15 | The PC runs out of storage space while receiving | automated (new in this pass) | `StorageFailureTest.test_a_folder_that_cannot_be_written_reports_the_offset_it_keeps`, `test_a_write_that_fails_halfway_keeps_the_good_prefix`, `test_a_full_inbox_does_not_report_a_completed_file` | Write failures are answered 503/507 with the real offset, the row becomes *Interrupted* with the OS reason in `error`, no half-copied file is left in the inbox, the partial survives, and once space exists the same transfer finishes byte-exact. Before this test existed, a full disk escaped as an unhandled `OSError` and looked like a network drop. |
| 16 | First run on a machine that has never seen LocalDrop | automated + manual once | `FirstRunTest.test_a_new_machine_builds_its_own_space` | From an empty folder LocalDrop creates `config.json`, the database, `incoming/` and `tmp/`, picks its own 6-digit code and keeps it across restarts; a device pairs against that code. The Windows side (double-clicking `LocalDrop.bat` on a clean machine) is documented and was run here only. |

---

## Checks that are not in the sixteen

| Subject | How | Evidence |
| --- | --- | --- |
| HTTP transport: keep-alive, `Expect: 100-continue`, malformed requests, bad JSON, unknown paths, oversized headers | automated | `TransportTest.*` |
| Static assets: `ETag`/`If-None-Match` → 304, correct MIME, no path outside `web/` | automated | `App._static`, covered by `TransportTest.test_security_headers_and_server_signature` |
| QR code correctness (finder/timing/format/version geometry, mask selection, capacity table, round-trip decode at every level) | automated | `tests/test_qr.py` (14 cases), byte-mode payloads decoded back to the URL |
| The page contains no post-ES5 syntax, no post-2015 browser API and no modern-only CSS | automated | `tests/test_compat.py` (25 cases). It also self-checks: synthetic modern JavaScript and CSS samples are fed to the same scanners and must be rejected, so the audit cannot silently stop looking. |
| Every `$("id")` in the client exists in the markup, every `data-close` target exists, no external asset | automated | `HtmlCompatibilityAudit.test_no_dangling_element_ids` and friends. This check found three real defects on the run that added it: the touch-mode drop-zone text was written to two elements that did not exist (so an iPad kept being told to "drag and drop"), the settings sheet had no pairing-code readout, and the rotate handler used the wrong XHR callback arity, which made it print "New pairing code undefined" and blank the code. |
| Launcher window: start/stop, port release, HTTP answer, QR, clipboard, PIN rotation, firewall report, sign-in command, close behaviour | manual once (headless probe) | `.scratch/launcher_probe.py`, 24 assertions, re-run against the deep-teal redesign: all passed. It is a development probe, not a shipped test. |
| Launcher looks like the web page | manual once (screenshot) | The window was opened started and photographed: teal brand mark, porcelain paper, white cards with hairline teal borders, teal PIN and Copy, soft-teal secondary buttons, dark-ink QR. Same tokens as `web/app.css`. |
| Receiver-side pause of a ready / downloading transfer | automated | `tests/test_transfers.py::test_pause_before_download_holds_the_file`, `test_pause_mid_download_keeps_the_bytes_already_received` — a destination can hold a ready file and pause a Range-chunk download keeping received bytes. |
| Live ↑/↓ speed + ETA on the moving row, hidden when idle | browser-driven | A 200 MB `File` sent through the real Uploader; `.row-speed` sampled mid-upload read "43.8 MB/s … 1s left" with the ↑ arrow and `bar is-live`, and was hidden with state *Delivered* afterwards. The watching side derives its figure from the server offset (`watchRate`); the ↓ path shares the same render code with the ↓ arrow. |
| Responsive layouts at phone / tablet / laptop widths | browser-driven | Same-origin iframes at 390 / 820 / 1280 px: at 1280 the rail is `float:left; width:320px` with the queue at `margin-left:344px`; at 820 and 390 the rail collapses to `float:none; width:auto` and the queue margin to 0 (single column). |
| Window layout at the sizes a laptop has | manual once | `.scratch/layout_probe.py` measured 695×679 needed against a 760×680 window. |
| Private-browsing behaviour (Safari refuses `localStorage`) | code + manual reasoning, **not** tested live | The client falls back to an in-memory session, the pairing screen says what that means, and a reload there needs the code again. Reproduce by opening the page in Safari Private mode. |
| `LocalDrop.bat`, `LocalDrop-console.bat`, `python -m localdrop.firewall status` | manual once | `LocalDrop-console.bat --url-only` printed the LAN URL; the firewall CLI reported `blocked (port 8080)` with the exact `netsh` command. `scripts/firewall-open.bat` raises a UAC prompt and was deliberately not run here. |
| Double-click entry point actually opens a window | a real failure on the user's PC, then fixed | `LocalDrop.bat` preferred `py -3 -w`, but the py launcher forwards `-w` to `python.exe`, which answers `Unknown option: -w` — so on any machine with `py` on PATH the double-click silently did nothing. The bat now prefers `pythonw` via `start ""` (no console left behind), then `py -3`, then `python`. Verified: `cmd /c LocalDrop.bat` left a live `pythonw` process and the window on screen. |
| Firewall report versus the network's own category | automated + a real failure on the user's PC | `tests/test_firewall.py` (14 cases). Found live: the launcher window was started with `--start`, `GET /` answered 200 on both `127.0.0.1:8080` and `192.168.1.10:8080`, the QR encoded the right URL, and no other device could load the page. `netsh` showed three enabled `LocalDrop` rules with `Profiles: Private` while `Get-NetConnectionProfile` showed `AFG DSL / Wi-Fi / Public` — the rule was dormant and the launcher still said "allowed". `status()` now returns `mismatch` and names the fix; re-running the CLI on that machine printed `mismatch (port 8080): … Windows is treating this network as public …`. The user then chose to keep the network Public, so the shipped rule became `profile=any remoteip=localsubnet` — every category, narrowed to the PC's own subnet — and `AddCommandTest` pins that scope plus the fact that the elevation path runs exactly the command it prints. |
| Cancelled firewall prompt leaving BLOCK rules for Python | automated + a real failure on the user's LAN | `tests/test_firewall.py` (5 cases). Live: with the correct `profile=any remoteip=localsubnet` rule in place, the phone, a second laptop and the iPad all still spun forever. `Get-NetFirewallRule -Direction Inbound -Action Block` showed two enabled `TCP/UDP Query User{…}` rules for `python.exe` on the Public profile — the residue of a cancelled "blocked some features" prompt, and block overrides allow. `status()` now reports `program-blocked` and names them; `scripts/firewall-open.bat` deletes exactly those auto-created rules. |
| iOS 9 Camera cannot scan the QR at all | not tested on hardware, documented | QR scanning in the Camera app arrived in iOS 11. On iOS 9 the address has to be typed into Safari, or a third-party scanner used. The launcher shows each URL as a selectable, copyable field in the same window for exactly this reason. |

## What is still unproven

* **Any real Apple hardware.** No iPad, iPhone or macOS machine was available.
  The iOS 9 claims rest on the ES5/CSS audit, the emulated old-Safari client and
  the browser-driven session — not on Safari itself.
* **`File.slice` under iPad 2 memory pressure.** The theory is that chunking
  keeps the footprint small; the only way to know is a real device with a real
  2 GB file.
* **iBooks/Books actually opening an EPUB** from LocalDrop's
  `Content-Disposition`, and iOS's download manager choosing to resume a
  stopped download.
* **Throughput and battery** on 802.11n hardware.
* **Multi-weekend soak**: many interrupted transfers, an enormous queue, and a
  `data/` directory with thousands of finished files.
* **A second Windows PC, a second network band, and a router with AP isolation**
  — the three environment factors that break LAN transfers most often.
