# Recorded responses

Real replies from outside services, written by `python -m scripts.record_run` and replayed with
`tests/fakes/recorded.py`. One line each: what it is, when it was recorded and by whom.
See `docs/setup.md`.

- `gate_air_fryer`: real replies to "air fryer for a family of four under 8000 rupees" (1 run; calls gemini: 7, groq: 5, mistral: 2, pages: 14, serper: 7). Recorded 2026-11-19 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_earbuds`: real replies to "wireless earbuds for the gym under 3000 rupees" (1 run; calls gemini: 8, groq: 7, mistral: 2, pages: 17, serper: 7). Recorded 2026-11-19 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_laptop`: real replies to "laptop for coding under 70000 rupees" (1 run; calls gemini: 8, groq: 4, mistral: 2, pages: 20, serper: 7). Recorded 2026-11-19 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_smartphone`: real replies to "phone with a good camera under 30000 rupees" (1 run; calls gemini: 7, groq: 6, mistral: 2, pages: 19, serper: 7). Recorded 2026-11-19 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_smartwatch`: real replies to "smartwatch for running with GPS under 15000 rupees" (1 run; calls gemini: 6, groq: 6, mistral: 2, pages: 13, serper: 7). Recorded 2026-11-19 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `laptop_coding_70k`: real replies to "best laptop for coding under 70k" (1 run; calls groq: 2). Recorded 2026-11-07 by Harsh Kodavala.
- `research_laptop_coding_70k`: real replies to "best laptop for coding under 70k" (1 run; calls groq: 2, serper: 6). Recorded 2026-11-08 by Jenish Kavad.
