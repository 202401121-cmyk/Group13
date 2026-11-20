# Recorded responses

Real replies from outside services, written by `python -m scripts.record_run` and replayed with
`tests/fakes/recorded.py`. One line each: what it is, when it was recorded and by whom.
See `docs/setup.md`.

- `gate_air_fryer`: real replies to "air fryer for a family of four under 8000 rupees" (1 run; calls gemini: 9, groq: 2, mistral: 2). Recorded 2026-11-20 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_earbuds`: real replies to "wireless earbuds for the gym under 3000 rupees" (1 run; calls gemini: 9, groq: 4, mistral: 2). Recorded 2026-11-20 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_laptop`: real replies to "laptop for coding under 70000 rupees" (1 run; calls gemini: 10, groq: 1, mistral: 2). Recorded 2026-11-20 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_smartphone`: real replies to "phone with a good camera under 30000 rupees" (1 run; calls gemini: 10, groq: 2, mistral: 2). Recorded 2026-11-20 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `gate_smartwatch`: real replies to "smartwatch for running with GPS under 15000 rupees" (1 run; calls gemini: 7, groq: 3, mistral: 2). Recorded 2026-11-20 by Harsh Kodavala. Some calls failed or were refused: see manifest.json.
- `laptop_coding_70k`: real replies to "best laptop for coding under 70k" (1 run; calls groq: 2). Recorded 2026-11-07 by Harsh Kodavala.
- `research_laptop_coding_70k`: real replies to "best laptop for coding under 70k" (1 run; calls groq: 2, serper: 6). Recorded 2026-11-08 by Jenish Kavad.
