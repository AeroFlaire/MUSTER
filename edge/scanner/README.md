# Phone CAC scanner (PBI-12)

A web page that reads a CAC barcode with the phone's camera. It reads the **front
(PDF417)** barcode, and the back (Code 39) as a fallback. It pulls out the DoD ID and
name **on the phone**, and shows the name and the last 4 digits of the ID. It isn't
connected to the API yet. That's the next step, and it goes to the Gatekeeper over HTTPS.

| File | What it does |
|---|---|
| `index.html`, `scanner.css`, `scanner.js` | The page: camera, guide box, decode loop, result screen |
| `cac_parse.js` | Turns a scanned string into `{edipi, firstName, middleInitial, lastName, category, branch}`; never returns DOB or card dates |
| `vendor/zxing-wasm-3.1.5/` | The barcode reader (zxing-cpp compiled to WebAssembly, MIT license), kept in the repo so the page loads nothing from outside sites |
| `serve.py` | Small HTTPS server for the page, with a self-signed certificate |
| `tests/` | Parser tests on synthetic barcodes in the real CAC layouts |

## Privacy rules (what the code enforces)

- The raw barcode string is used once to parse, then dropped. It's **never shown, logged,
  saved, or sent**. `serve.py` only ever sees requests for the page's own files.
- The screen shows the name and `******1234`, then clears itself after 60 seconds. The
  camera stops as soon as the page is hidden.
- The page's security policy (sent by `serve.py`) blocks it from contacting **any** other
  site, so even a bug can't send a scan anywhere.
- Real CACs are scanned **only by their owner**, and nothing is saved (PBI-12's DoD change).
  Never paste a real barcode string into an issue, a chat, a test, or this repo.

## Run it on your phone

Phones only allow the camera on `https://` pages.

```powershell
pip install cryptography            # used to make the self-signed certificate
python edge\scanner\serve.py
```

It prints addresses like `https://192.168.x.x:8443/`.

1. Put the phone and laptop on the **same network**. If the Army network blocks it, turn on
   the phone's hotspot and join it from the laptop.
2. Open the printed `https://...:8443/` address on the phone.
3. The phone warns about the certificate (it's self-signed). Accept it once:
   - **iPhone:** Show Details, then visit this website.
   - **Android Chrome:** Advanced, then Proceed.
4. Tap **Start camera** and allow the camera.
5. Hold the card flat, 4 to 6 inches away, with the wide barcode on the front inside the box.
   A tilt of up to about 15 degrees is fine.

**If Windows Firewall asks about Python, allow it on Private networks.** If the phone still
can't connect, the network is blocking device-to-device traffic. Use the hotspot.

**Laptop webcam only:** `python edge\scanner\serve.py --http --host 127.0.0.1` and open
`http://localhost:8080/`. Browsers treat localhost as secure, so the camera works.

## Test cards (synthetic)

```powershell
pip install zxing-cpp reportlab pillow numpy
python scripts\make_front_test_cards.py      # -> test_front_cards.pdf (13 cards, front + back)
```

These are fake DoD IDs 9000000001–9000000013 in the real layouts. The last three are edge
cases: no middle initial, a last name with spaces, and a hyphenated last name. Print them,
or show the PDF on a second screen.

## Tests

```powershell
node --test edge\scanner\tests\cac_parse.test.cjs
```

What was checked before this was committed:

- **Parser tests:** 7 of 7 pass.
- **Real cards:** the parser was checked on two real cards. Front and back gave the same
  DoD ID, and the names were right. This was done locally, and the strings were not kept.
- **Printed test cards:** all 26 barcodes (13 fronts, 13 backs) decode.
- **Mobile Chromium with a simulated camera:**
  - Fronts read in 0.2–0.9 s, including cards tilted 6°, 10° and 16°.
  - A non-CAC PDF417 barcode is rejected, and scanning continues.
  - The page contacted only its own server, and no full DoD ID appeared on screen.

## Known limits

- **The front layout comes from real cards, not a published spec.** Version "M" is what
  current cards carry, and the older "N" format is rejected with a message. PBI-12 asks for
  5 cards. Each new owner should scan their own card and confirm the name and last 4.
- **A first name containing a space** (for example "Mary Ann") is split at the first space,
  so part of it lands in the last name. The DoD ID is never affected. The grader confirms the
  name on screen.
- **The front barcode also holds date of birth and card dates.** The parser ignores them, and
  nothing downstream should ever ask for them.

The earlier attempt (`edge/scanner.html` + `edge/server.py`) is replaced by this folder.
`edge/server.py` prints the raw barcode to its log, so don't use it with a real card.
