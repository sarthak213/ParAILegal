# ParAILegal — Frontend

The frontend for [ParAILegal](https://github.com/sarthak213/ParAILegal), an AI-powered Indian legal research tool. Built with React, TypeScript, and Vite.

## What it does

ParAILegal lets lawyers, law students, and researchers ask questions about Indian law and get grounded, cited answers in real time. Every answer traces directly to primary sources — the Constitution of India, the BNS 2023, BNSS 2023, BSA 2023, and landmark Supreme Court judgements.

## Features

- **Streaming answers** — responses stream token by token via Server-Sent Events
- **Inline citations** — every `[Article 21]` or `[Section 103, BNS]` tag is clickable and highlights the source
- **Sources sidebar** — retrieved legal chunks ranked by relevance, with expand and copy-citation actions
- **Three research modes** — Research, Advocate (counter-argument), Summarise (plain language)
- **Domain filtering** — search All, Constitution, Statutes, or Judgements
- **Session history** — past queries persist in localStorage, resumable with one click
- **Export to PDF** — print a clean formatted research report
- **Copy citation** — one-click legal citation formatting

## Stack

- React 18 + TypeScript
- Vite
- Lucide React (icons)
- CSS custom properties (no UI framework)
- Fonts: Cormorant Garamond, Instrument Sans, DM Mono

## Getting started

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The app runs at `http://localhost:5173` and connects to the FastAPI backend at `http://localhost:8000` by default.

## Backend

The backend repo is at [github.com/sarthak213/ParAILegal](https://github.com/sarthak213/ParAILegal). It must be running locally for the frontend to work. Start it with:

```bash
uvicorn app.main:app --reload
```

## Configuration

Create a `.env.local` file in the project root:

```env
# Local development
VITE_API_BASE_URL=http://localhost:8000

# Production (Oracle Cloud VM)
# VITE_API_BASE_URL=http://YOUR_SERVER_IP
```

## Building for production

```bash
npm run build
```

Output goes to `dist/`. Deploy to GitHub Pages or any static host.

## Project structure

```text
src/
├── components/
│   ├── answer/       # AnswerView with streaming and citation rendering
│   ├── layout/       # Sidebar with history
│   ├── query/        # QueryInput with mode and domain controls
│   └── sources/      # SourceCard and SourcesPanel
├── hooks/
│   ├── useStream.ts  # SSE streaming logic
│   ├── useHistory.ts # localStorage CRUD
│   └── useBackend.ts # API health check
├── types/            # TypeScript interfaces
└── utils/
    ├── citations.ts  # Legal citation formatting and parsing
    └── export.ts     # PDF export and clipboard utilities
```

## License

GNU Affero General Public License v3.0 — see the backend repo for full license terms.
