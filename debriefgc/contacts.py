from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from .config import Config


class ContactsLookupError(RuntimeError):
    pass


CONTACTS_JXA = r'''
ObjC.import("Contacts");

function unwrap(value) {
  return ObjC.unwrap(value) || "";
}

function contactsAccessGranted(store) {
  const contactEntityType = 0;
  const authorized = 3;
  const limited = 4;
  const status = $.CNContactStore.authorizationStatusForEntityType(contactEntityType);
  if (status === authorized || status === limited) {
    return true;
  }

  let done = false;
  let granted = false;
  store.requestAccessForEntityTypeCompletionHandler(contactEntityType, (ok, error) => {
    granted = ok;
    done = true;
  });
  while (!done) {
    delay(0.1);
  }
  return granted;
}

const store = $.CNContactStore.alloc.init;
if (!contactsAccessGranted(store)) {
  throw new Error("Contacts access was not granted.");
}

const keys = $.NSArray.arrayWithArray([
  $.CNContactGivenNameKey,
  $.CNContactFamilyNameKey,
  $.CNContactPhoneNumbersKey,
  $.CNContactEmailAddressesKey,
]);
const request = $.CNContactFetchRequest.alloc.initWithKeysToFetch(keys);
const lines = [];
const error = Ref();

const ok = store.enumerateContactsWithFetchRequestErrorUsingBlock(
  request,
  error,
  (contact, stop) => {
    const firstName = unwrap(contact.givenName);
    const familyName = unwrap(contact.familyName);
    const fullName = `${firstName} ${familyName}`.trim();
    const displayName = firstName || fullName;

    for (let index = 0; index < contact.phoneNumbers.count; index++) {
      const phone = contact.phoneNumbers.objectAtIndex(index).value.stringValue;
      lines.push(["phone", unwrap(phone), displayName, fullName].join("\t"));
    }
    for (let index = 0; index < contact.emailAddresses.count; index++) {
      const email = contact.emailAddresses.objectAtIndex(index).value;
      lines.push(["email", unwrap(email), displayName, fullName].join("\t"));
    }
  }
);

if (!ok) {
  let detail = "";
  if (error[0]) {
    detail = unwrap(error[0].localizedDescription);
  }
  throw new Error(`Contacts enumeration failed${detail ? ": " + detail : "."}`);
}

lines.join("\n");
'''


def contact_lookup_keys(handle: str) -> list[str]:
    handle = handle.strip()
    if not handle or handle == "me":
        return [handle] if handle else []
    if "@" in handle:
        return [f"email:{handle.lower()}"]

    digits = re.sub(r"\D", "", handle)
    if not digits:
        return [handle]

    keys: list[str] = []
    if len(digits) == 10:
        keys.extend([f"phone:+1{digits}", f"phone:{digits}"])
    elif len(digits) == 11 and digits.startswith("1"):
        keys.extend([f"phone:+{digits}", f"phone:{digits}", f"phone:{digits[1:]}"])
    else:
        keys.extend([f"phone:+{digits}", f"phone:{digits}"])
    return list(dict.fromkeys(keys))


def load_macos_contacts() -> dict[str, str]:
    osascript_error = ""
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", CONTACTS_JXA],
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            osascript_error = (exc.stderr or exc.stdout or "").strip()
        else:
            osascript_error = str(exc)
        try:
            return load_addressbook_contacts()
        except ContactsLookupError as fallback_exc:
            message = "Could not query macOS Contacts."
            if osascript_error:
                message = f"{message}\nosascript: {osascript_error}"
            message = f"{message}\nAddressBook fallback: {fallback_exc}"
            raise ContactsLookupError(message) from exc

    names: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        kind, value, first_name, full_name = parts[:4]
        display_name = (first_name or full_name).strip()
        if not display_name:
            continue
        for key in contact_lookup_keys(value):
            if key.startswith(f"{kind}:"):
                names.setdefault(key, display_name)
    return names


def load_addressbook_contacts() -> dict[str, str]:
    addressbook_dir = Path("~/Library/Application Support/AddressBook").expanduser()
    db_paths = sorted(
        set(addressbook_dir.glob("AddressBook-v*.abcddb"))
        | set(addressbook_dir.glob("Sources/*/AddressBook-v*.abcddb"))
    )
    names: dict[str, str] = {}
    errors: list[str] = []

    for db_path in db_paths:
        try:
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                add_addressbook_rows(
                    names,
                    conn.execute(
                        """
                        SELECT
                          'phone' AS kind,
                          p.ZFULLNUMBER AS value,
                          r.ZFIRSTNAME AS first_name,
                          r.ZLASTNAME AS last_name,
                          r.ZNICKNAME AS nickname,
                          r.ZORGANIZATION AS organization
                        FROM ZABCDPHONENUMBER p
                        JOIN ZABCDRECORD r ON r.Z_PK = p.ZOWNER
                        WHERE p.ZFULLNUMBER IS NOT NULL
                        """
                    ),
                )
                add_addressbook_rows(
                    names,
                    conn.execute(
                        """
                        SELECT
                          'email' AS kind,
                          COALESCE(e.ZADDRESSNORMALIZED, e.ZADDRESS) AS value,
                          r.ZFIRSTNAME AS first_name,
                          r.ZLASTNAME AS last_name,
                          r.ZNICKNAME AS nickname,
                          r.ZORGANIZATION AS organization
                        FROM ZABCDEMAILADDRESS e
                        JOIN ZABCDRECORD r ON r.Z_PK = e.ZOWNER
                        WHERE COALESCE(e.ZADDRESSNORMALIZED, e.ZADDRESS) IS NOT NULL
                        """
                    ),
                )
        except sqlite3.Error as exc:
            errors.append(f"{db_path}: {exc}")

    if names:
        return names
    if errors:
        raise ContactsLookupError("; ".join(errors))
    raise ContactsLookupError(f"No AddressBook contact data found at {addressbook_dir}.")


def add_addressbook_rows(names: dict[str, str], rows) -> None:
    for kind, value, first_name, last_name, nickname, organization in rows:
        display_name = (
            (first_name or "").strip()
            or (nickname or "").strip()
            or " ".join(part for part in [first_name, last_name] if part).strip()
            or (organization or "").strip()
        )
        if not display_name:
            continue
        for key in contact_lookup_keys(str(value)):
            if key.startswith(f"{kind}:"):
                names.setdefault(key, display_name)


def load_contact_names(config: Config) -> dict[str, str]:
    if not config.contacts_enabled:
        return {}
    return load_macos_contacts()


def resolve_sender_name(
    sender_handle: str,
    config: Config,
    contact_names: dict[str, str] | None = None,
) -> str:
    if sender_handle in config.participant_names:
        return config.participant_names[sender_handle]
    if contact_names:
        for key in contact_lookup_keys(sender_handle):
            if key in contact_names:
                return contact_names[key]
    return sender_handle
