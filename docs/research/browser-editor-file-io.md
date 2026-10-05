# Browser editor file I/O: open, save, sibling file, autosave

Date: 2026-10-01. Ticket: #59 (child of map #58). Facts only; no recommendation.

Question: what can one self-contained HTML file do in Chrome, Safari, and Firefox to open a graph and a record from disk, save to the same file, write a sibling layout file, and autosave, with no server?

Sources are MDN pages, the owning specs, and the browser vendors' own position records. MDN compat figures come from the `mdn/browser-compat-data` JSON (read 2026-10-01). Statements marked UNVERIFIED have no primary source here.

## 1. File System Access: the pickers

`showOpenFilePicker()`, `showSaveFilePicker()`, and `showDirectoryPicker()` return `FileSystemFileHandle` or `FileSystemDirectoryHandle` objects. The handle is the only way to write back to the same file on disk.

Support (`mdn/browser-compat-data`, `api/Window.json`):

| Method | Chrome | Edge | Firefox | Safari |
|---|---|---|---|---|
| `showOpenFilePicker` | 86 | mirrors Chrome | not supported | not supported |
| `showSaveFilePicker` | 86 | mirrors Chrome | not supported | not supported |
| `showDirectoryPicker` | 86 | mirrors Chrome | not supported | not supported |

- Chrome on Android: 132. Firefox and Safari mobile mirror their desktop (not supported).
- MDN marks the pickers "Limited availability", "not Baseline", and experimental.
- Vendor positions. Mozilla: `position: negative` on "File System Access API" (mozilla/standards-positions issue 154). WebKit: `position: oppose`, concern `security` (WebKit/standards-positions issue 28). Both are Safari and Firefox records of the picker API.
- Each picker needs a secure context and transient user activation (MDN). Without activation it throws `SecurityError`.
- `showOpenFilePicker` options: `multiple`, `types`, `excludeAcceptAllOption`, `id`, `startIn`. It returns an array of handles.
- `showSaveFilePicker` options: `suggestedName`, `types`. It returns one handle. MDN does not say whether it prompts on an existing name.
- Both throw `AbortError` when the user dismisses the dialog or the browser deems the file dangerous.
- Chrome developer docs: the browser may refuse core OS folders (the Windows folder, the macOS Library folders) and asks for another choice.

## 2. Writing and permission

- `FileSystemFileHandle.createWritable()` returns a stream. MDN: changes go to a swap file and replace the real file only on `close()`. Until then the file is unchanged.
- It throws `NotAllowedError` if the handle lacks `readwrite` permission. `mode: "exclusive"` can throw `NoModificationAllowedError`. Default mode is `"siloed"`, last write wins.
- Support for `createWritable`: Chrome 86, Firefox 111, Safari 26 (BCD). `getFile`: Chrome 86, Firefox 111, Safari 15.2. These Firefox and Safari figures cover handles from the origin private file system (OPFS), not user files, because Firefox and Safari have no picker to obtain a user-file handle (section 1).
- `queryPermission` and `requestPermission`: Chrome 86, Firefox none, Safari none (BCD). `requestPermission` needs transient user activation.
- Chrome developer docs: after the user grants write permission, the app saves without prompts "until all tabs for its origin are closed. Once a tab is closed, the site loses all access."
- Handles are serializable into IndexedDB (MDN File System API page, Chrome docs). A stored handle needs a permission check on reuse.
- Sibling file: `FileSystemDirectoryHandle.getFileHandle(name, {create: true})` creates or opens a named file in a picked directory. It needs `readwrite` permission on that directory. Baseline widely available since March 2023 (MDN), but a directory handle needs `showDirectoryPicker` (Chrome only) or OPFS (private, not visible to the user).
- Without a directory handle, a second `showSaveFilePicker` call is the only picker route to a sibling file. It needs its own user activation.
- Autosave: after one grant, `createWritable()` then `write()` then `close()` can run with no new prompt in the same session (Chrome docs). The MDN `createWritable` page lists only the permission state as a precondition, not user activation. With no open tab for the origin, the grant is gone (Chrome docs, above).

## 3. `file://` limits

- Secure Contexts spec: a user agent "SHOULD treat `file` URLs as potentially trustworthy". It "MAY choose to more strictly assign trust in a way which excludes `file`." So the pickers are available at `file://` only where the browser treats it as secure. Chrome: UNVERIFIED for a primary statement. Firefox and Safari have no picker either way.
- MDN, same-origin policy: modern browsers usually treat `file:///` origins as opaque. A page cannot assume sibling files share its origin. Some browsers treat files in the same directory as same-origin, with security implications (Mozilla advisory mfsa2019-21, CVE-2019-11730).
- Consequence per MDN: `fetch` or XHR of a sibling file from a `file://` page may fail with CORS errors. A page cannot read the sibling layout file by URL. It needs a handle, an `<input type="file">`, or a drop.
- HTML Standard: the `localStorage` getter "Throws a `SecurityError` `DOMException` if the `Document`'s origin is an opaque origin". How each browser treats `localStorage` at `file://` in practice: UNVERIFIED (no primary page found).

## 4. Opening with no picker API

Works in Chrome, Firefox, and Safari (MDN, "Using files from web applications"):

- `<input type="file">` and its `files` list.
- Drag and drop: `e.dataTransfer.files`.
- `FileReader`, and `file.name`, `file.size`, `file.type`.
- `<input type="file" webkitdirectory>` picks a directory: Chrome 7, Firefox 50, Safari 11.1 (BCD).

These routes give a read-only `File`. They give no handle, so they cannot write back to the same path.

## 5. Downloads

- `<a download="name">` with a `blob:` or `data:` URL saves a file (MDN, `a` element). `download` works only for same-origin, `blob:`, and `data:` URLs.
- The value is a suggested filename. `/` and `\` become `_`. The browser may adjust other characters.
- MDN does not say a download overwrites an existing file. Naming conflicts and the save location follow the user's browser settings (MDN: behavior varies by browser settings). Overwrite behavior: UNVERIFIED.
- A download is a new file each time. It is not a write to the opened file.

## 6. `localStorage` and other storage

- MDN, storage quotas: "Web Storage ... is limited to 10 MiB of data maximum on all browsers", that is 5 MiB `localStorage` and 5 MiB `sessionStorage` per origin.
- Web Storage is synchronous and stores per origin. Private browsing makes `localStorage` behave like `sessionStorage` (deleted on close) (MDN Web Storage API).
- Safari: if an origin has no user interaction in the last seven days of browser use, script-written data is deleted when cross-site tracking prevention is on (MDN, quotas and eviction; WebKit rule). Server-set cookies are exempt.
- IndexedDB, Cache API, and OPFS quotas (MDN): Chrome up to 60% of disk per origin; Firefox best-effort the smaller of 10% of disk or 10 GiB; Safari about 60% of disk in a browser app (macOS 14, iOS 17 and later).
- OPFS (`navigator.storage.getDirectory()`): Chrome 86, Firefox 111, Safari 15.2 (BCD). MDN: "private to the origin of the page and not visible to the user". It is not the user's file.

## 7. Matrix

| Need | Chrome and Edge | Firefox | Safari |
|---|---|---|---|
| Open with a writable handle | picker, 86 | none | none |
| Open read-only | picker, input, drop | input, drop | input, drop |
| Save back to the same file | handle + `createWritable` | none | none |
| Write a sibling file, no prompt | directory handle, `create: true` | none | none |
| Write a new file | picker or download | download | download |
| Autosave to disk | after one grant, per session | none | none |
| Autosave to the browser | `localStorage` 5 MiB, IndexedDB, OPFS | same | same, with 7-day eviction |

## Unverified

- Whether Chrome treats a `file://` page as a secure context for the pickers. The spec only allows it.
- `localStorage` behavior at `file://` in each browser.
- Whether a download overwrites a same-named file.
- Whether `showSaveFilePicker` warns before replacing an existing file.
- Safari 26 `createWritable` for non-OPFS handles. Without a picker none exists.

## Sources

- https://developer.mozilla.org/en-US/docs/Web/API/File_System_Access_API
- https://developer.mozilla.org/en-US/docs/Web/API/Window/showOpenFilePicker
- https://developer.mozilla.org/en-US/docs/Web/API/Window/showSaveFilePicker
- https://developer.mozilla.org/en-US/docs/Web/API/FileSystemFileHandle/createWritable
- https://developer.mozilla.org/en-US/docs/Web/API/FileSystemHandle/requestPermission
- https://developer.mozilla.org/en-US/docs/Web/API/FileSystemDirectoryHandle/getFileHandle
- https://developer.mozilla.org/en-US/docs/Web/API/Web_Storage_API
- https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/a
- https://developer.mozilla.org/en-US/docs/Web/API/File_API/Using_files_from_web_applications
- https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy
- https://html.spec.whatwg.org/multipage/webstorage.html
- https://w3c.github.io/webappsec-secure-contexts/
- https://developer.chrome.com/docs/capabilities/web-apis/file-system-access
- https://github.com/mdn/browser-compat-data (`api/Window.json`, `FileSystemFileHandle.json`, `FileSystemHandle.json`, `StorageManager.json`, `HTMLInputElement.json`)
- https://github.com/mozilla/standards-positions/issues/154
- https://github.com/WebKit/standards-positions/issues/28
