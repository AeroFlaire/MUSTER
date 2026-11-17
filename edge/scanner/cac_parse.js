/*
 * CAC barcode parser (PBI-12). Runs in the browser and in Node (for tests).
 *
 * WHAT IT READS
 *   - FRONT  (PDF417, version "M"): DoD ID + name, plus other card fields we ignore.
 *   - BACK   (Code 39, version "1", 18 characters): DoD ID, no name.
 *
 * WHAT IT RETURNS (and nothing else, on purpose)
 *   { side, edipi, firstName, middleInitial, lastName, category, branch }
 *   The barcode also holds a date of birth, card dates and internal card numbers.
 *   The parser never returns those, so nothing downstream can show or store them.
 *
 * FRONT LAYOUT (version "M"), worked out from real cards and NOT from a published spec,
 * so it is only as good as the cards we have checked. Positions are 0-based.
 *
 *   [0]        "M"            format version
 *   [1..7]     7 chars        DoD ID (EDIPI), base 32 ("0-9" then "A-V")  <-- not plain digits
 *   [8..15]    8 chars        other card data (ignored)
 *   [16..L-38] variable       FIRST + " " + MIDDLE INITIAL (1 char) + LAST
 *   [L-37]     " "            separator
 *   last 36    fixed width    DOB(4) "US"(2) " "(1) CATEGORY(1) BRANCH(1) ...rest ignored
 *
 *   Names are separated by spaces, so a first or last name that itself contains a
 *   space ("Mary Ann", "De La Cruz") cannot be split with certainty. We split at the
 *   FIRST space (first name has no spaces), which is right for "De La Cruz" and wrong
 *   for "Mary Ann". The grader sees the name and confirms it, so a wrong split is
 *   caught by a person, and the DoD ID (fixed position) is never affected.
 *
 * BACK LAYOUT (version "1"), from the public CACBarcode project (jkusner/CACBarcode):
 *   "1" + PDI(6) + PDI type(1) + EDIPI base 32 (7) + CATEGORY(1) + BRANCH(1) + card instance(1)
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CacParse = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const B32 = "0123456789ABCDEFGHIJKLMNOPQRSTUV";
  const FRONT_TAIL = 36;              // fixed-width block at the end of a front barcode
  const FRONT_NAME_START = 16;
  const FRONT_MIN_LEN = FRONT_NAME_START + 3 + 1 + FRONT_TAIL;  // "A B" name minimum

  const CATEGORY = {
    A: "Active duty", B: "Presidential appointee", C: "DoD civil service", D: "Disabled veteran",
    E: "DoD contractor", F: "Former member", G: "National Guard", H: "Medal of Honor",
    I: "Non-DoD civil service",
    J: "Academy student", K: "Non-appropriated fund employee", L: "Lighthouse service",
    M: "Non-government agency", N: "National Guard", O: "Non-DoD contractor",
    Q: "Reserve retiree", R: "Retired", S: "Reserve", T: "Foreign military",
    U: "Foreign national employee", V: "Reserve", W: "DoD beneficiary", Y: "Retired DoD civil service",
  };
  // Codes from the DoD ID bar code SDK formats document, as listed in jkusner/CACBarcode.
  const BRANCH = {
    A: "Army", C: "Coast Guard", D: "DoD", F: "Air Force", H: "Public Health Service",
    M: "Marine Corps", N: "Navy", O: "NOAA", 1: "Foreign Army", 2: "Foreign Navy",
    3: "Foreign Marine Corps", 4: "Foreign Air Force", X: "Other",
  };

  class CacParseError extends Error {}

  /** Base 32 ("0-9A-V") to a 10-digit DoD ID string. */
  function decodeEdipi(seven) {
    if (!/^[0-9A-V]{7}$/.test(seven)) throw new CacParseError("DoD ID field is not valid base 32");
    let n = 0;
    for (const ch of seven) n = n * 32 + B32.indexOf(ch);
    if (n < 1 || n > 9999999999) throw new CacParseError("DoD ID field is out of range");
    return String(n).padStart(10, "0");
  }

  function cleanName(s) {
    return s.replace(/\s+/g, " ").trim();
  }

  function parseFront(raw) {
    if (raw.length < FRONT_MIN_LEN) throw new CacParseError("too short for a CAC front barcode");
    const edipi = decodeEdipi(raw.slice(1, 8));
    if (raw[raw.length - FRONT_TAIL - 1] !== " ")
      throw new CacParseError("front barcode layout not recognized (name block)");
    const tail = raw.slice(raw.length - FRONT_TAIL);
    const nameBlock = raw.slice(FRONT_NAME_START, raw.length - FRONT_TAIL - 1);
    const sp = nameBlock.indexOf(" ");
    if (sp < 1 || sp + 2 > nameBlock.length)
      throw new CacParseError("front barcode layout not recognized (no first name)");
    const firstName = cleanName(nameBlock.slice(0, sp));
    const mi = nameBlock[sp + 1];
    const lastName = cleanName(nameBlock.slice(sp + 2));
    if (!lastName) throw new CacParseError("front barcode layout not recognized (no last name)");
    if (!/^[A-Za-z ]$/.test(mi)) throw new CacParseError("front barcode layout not recognized (middle initial)");
    return {
      side: "front",
      edipi,
      firstName,
      middleInitial: mi.trim(),
      lastName,
      category: tail[7],
      branch: tail[8],
    };
  }

  function parseBack(raw) {
    if (raw.length !== 18) throw new CacParseError("a CAC back barcode is 18 characters");
    return {
      side: "back",
      edipi: decodeEdipi(raw.slice(8, 15)),
      firstName: null,
      middleInitial: null,
      lastName: null,
      category: raw[15],
      branch: raw[16],
    };
  }

  /** Parse a scanned CAC barcode. Throws CacParseError with a reason that never quotes the input. */
  function parseCac(raw) {
    if (typeof raw !== "string" || raw.length === 0) throw new CacParseError("empty scan");
    if (raw[0] === "M") return parseFront(raw);
    if (raw[0] === "1") return parseBack(raw);
    if (raw[0] === "N")
      throw new CacParseError("older CAC front format (version N) is not supported yet");
    throw new CacParseError("not a CAC barcode");
  }

  /** "******1234": what a screen may show. */
  function maskEdipi(edipi) {
    return "******" + String(edipi).slice(-4);
  }

  return { parseCac, maskEdipi, decodeEdipi, CacParseError, CATEGORY, BRANCH };
});
