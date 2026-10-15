# Provenance, Asset Origin & Architectural Adaptation

## 1. Upstream Source Repository & Author Attribution

- **Source Repository**: `https://github.com/Hopeelex/keytool-tracker`
- **Original Author**: Teammate `adavid` ([@Hopeelex](https://github.com/Hopeelex))
- **Commit Baseline**: `9552053c265bc840d7fde666ea9a952518eb4583`
- **Team Authorization**: The original author personally granted explicit authorization to integrate the dashboard layout, visual components, styling tokens, and QR/WebRTC pairing workflow directly into the ToolLightHouse academic project.
- **Licensing Scope**: This authorization is an explicit permission for team project integration and mutual academic collaboration. It does not constitute an arbitrary open-source relicensing of the upstream standalone repository. Upstream copyrights are preserved, and attribution is maintained throughout this project.
- **Reference Repository Isolation**: The reference repository was cloned into an isolated temporary directory (`$env:TEMP/keytool-tracker-ref`) strictly for visual and component inspection, and was not modified in any way.

---

## 2. Upstream Files & Adapted ToolLightHouse Destinations

| Upstream Path (`keytool-tracker`) | Adaptation Type | ToolLightHouse Destination | Architectural Differences & Improvements |
| --- | --- | --- | --- |
| `src/App.jsx` (layout & pairing) | Conceptual & structural adaptation into TypeScript | `frontend/src/App.tsx`, `frontend/src/components/RemoteCameraView.tsx`, `frontend/src/components/QRModal.tsx` | Upstream was a single monolithic JSX file using third-party PeerJS Cloud with hardcoded mock tools. ToolLightHouse migrated logic to modular React 19 + TypeScript (TSX) components, native browser `RTCPeerConnection` connected to a local FastAPI signaling hub (`/v1/camera/signal`), and dynamic 9-tool inventory from the backend. |
| `src/App.css`, `src/index.css` | Class & token extraction | `frontend/src/styles.css` | Adopted brand color tokens: purple accents (`#7E22CE`, `#9333EA`), dark video background (`#0A0F1C`), gray-white rounded cards, status pills, and responsive 2-column tablet layout. |
| QR Code Pairing Flow | Secure offline reimplementation | `frontend/src/components/QRModal.tsx`, `frontend/src/api.ts` | Upstream relied on external PeerJS broker IDs. ToolLightHouse implements local, cryptographic single-use expiring invitations (`POST /v1/camera/invite`), rendered via the offline `qrcode` npm package, with hash fragment (`#token=...`) scrubbing upon consumption. |
| Video Viewport & Camera | Architectural adaptation | `frontend/src/components/RemoteCameraView.tsx` | Dedicated `/camera` SPA view with `playsInline`, rear camera capture (`facingMode: "environment"`), best-effort `navigator.wakeLock`, and rate-limited JPEG fallback (`POST /v1/camera/frame-upload`). |
| 3×3 Grid Concept | Data-driven component rewrite | `frontend/src/App.tsx` (Inventory panel) | KeyTool Tracker hardcoded mock item names. ToolLightHouse binds the 3×3 grid to real backend tool configurations, LED pin assignments (D2–D10), hardware ACK statuses, and cosine similarity margins. |
| Navigation & Admin Panels | Total preservation | `frontend/src/App.tsx`, `frontend/src/components/` | Preserved all 5 existing ToolLightHouse administrative sections: *Inspección*, *Herramientas*, *Calibración*, *Arduino*, and *Sistema*. |

---

## 3. Third-Party Dependencies & Bundled Assets

1. **Albert Sans Typography**:
   - **Designer**: Andreas Rasmussen.
   - **License**: [SIL Open Font License 1.1 (OFL-1.1)](https://scripts.sil.org/OFL).
   - **Distribution**: Bundled offline via the `@fontsource/albert-sans` package; no Google Fonts or external CDNs contacted at runtime.

2. **Lucide Icons**:
   - **License**: [ISC License](https://github.com/lucide-icons/lucide/blob/main/LICENSE).
   - **Package**: `lucide-react`.

3. **QR Code Engine**:
   - **License**: MIT License.
   - **Package**: `qrcode` (offline SVG/canvas generation without cloud services).
