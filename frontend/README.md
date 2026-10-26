# TraceRule AI Frontend

A modern React-based web dashboard for the TraceRule AI regulatory intelligence system.

## Features

- **Dashboard**: Overview with stats, recent activity, and rule status breakdown
- **Documents**: Browse and filter regulatory documents
- **Evidence**: View evidence passages from documents
- **Phase 5 Extraction**: Run AI extraction on evidence passages
- **Candidates**: Review and approve/reject extracted candidates
- **Phase 6 Governance**: Manage rule lifecycle (draft → test → pending → approved → retired)
- **Audit Trail**: View complete governance event history

## Tech Stack

- React 18 with Vite
- Tailwind CSS for styling
- React Router for navigation
- Lucide React for icons
- Recharts for charts

## Prerequisites

- Node.js 18+
- npm or yarn

## Installation

```bash
# Install dependencies
npm install
```

## Running the Development Server

```bash
npm run dev
```

The app will be available at `http://localhost:5173`

## Building for Production

```bash
npm run build
```

The production build will be in the `dist` folder.

## Project Structure

```
frontend/
├── src/
│   ├── components/
│   │   └── Layout.jsx         # Main layout with sidebar
│   ├── pages/
│   │   ├── Dashboard.jsx      # Dashboard overview
│   │   ├── Documents.jsx      # Documents listing
│   │   ├── Evidence.jsx       # Evidence passages
│   │   ├── Phase5Extraction.jsx # Phase 5 extraction workflow
│   │   ├── Candidates.jsx     # Candidate review
│   │   ├── Governance.jsx     # Phase 6 governance
│   │   └── AuditTrail.jsx     # Audit trail viewer
│   ├── App.jsx                # Main app with routing
│   ├── main.jsx               # Entry point
│   └── index.css              # Global styles
├── index.html
├── package.json
├── vite.config.js
├── tailwind.config.js
└── postcss.config.js
```

## Connecting to Backend

The frontend is configured to proxy API requests to `http://localhost:8000`. To connect to a real backend:

1. Update `vite.config.js` with your backend URL
2. Create API service functions in `src/services/`
3. Replace mock data in pages with real API calls

## Screenshots

See the design proposals in the main project docs for UI mockups.