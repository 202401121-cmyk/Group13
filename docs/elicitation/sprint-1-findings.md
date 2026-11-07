# Sprint 1 elicitation findings

Status on 6 November 2026: the API sign-ups and limits are written up. The survey, interview, observation and expert
interview results are pending, because their notes and the survey export have not been collected yet. No survey
number appears here until it comes from the counting script.

Exit criterion 8 asks for 30 or more survey responses. The count is not confirmed yet (pending: Jhanvi Barot).

## Method

| Technique | Target | Owner | Status |
|---|---|---|---|
| Survey (Google Form) | 30 or more responses | Jhanvi Barot | pending: Jhanvi Barot |
| Shopper interviews | 3, about 20 minutes each | Om Pandya, Rushiraj Navar, Jenish Kavad | pending: Om Pandya, Rushiraj Navar, Jenish Kavad |
| Observation | 1 real purchase, thinking aloud | Harshkumar Kodavala | pending: Harshkumar Kodavala |
| Expert interview | 1 laptop or phone expert | Pal Jalodara | pending: Pal Jalodara |
| API sign-ups and limits | Every outside service | Jenish Kavad | Done, recorded on 3 November 2026 |

The questions and note templates are in [the elicitation kit](../elicitation-kit.md). People are anonymised:
survey respondents are only counted, interviewees are P1, P2 and P3, and the expert is "the expert" with their field.
Raw survey data and notes are never added to the repository.

## Survey counts

Remove every column that identifies a person from the export, keep the file outside the repository, then run this from
the repository root:

```bash
python scripts/elicitation/count_survey.py <path to the CSV>
```

It prints the response count and each answer in the form "21 of 34 (62%)". Free-text answers are counted, never
printed. It stops without reading any answer if a column still identifies a person.

Questions 4 to 7 test design decisions, so each is linked to the requirement it supports or changes.

| Question | Decision it tests | Links | Result |
|---|---|---|---|
| 4. How many questions would you answer before getting recommendations? | Interview length | FR-05 (US-05), FR-04 (US-04) | pending: Jhanvi Barot |
| 5. How important is it to see the source of each claim? | Citations | FR-18 (US-21), NFR-14 | pending: Jhanvi Barot |
| 6. Should the app remember your preferences for next time? | Memory | FR-06 (US-06) | pending: Jhanvi Barot |
| 7. If two products are nearly tied, would you rather… | Ties | FR-16 (US-19) | pending: Jhanvi Barot |

Counts for questions 1, 2, 3, 8 and 9: pending: Jhanvi Barot.

## Findings from each method

- **Shopper interviews (P1, P2, P3):** pending: Om Pandya, Rushiraj Navar, Jenish Kavad.
- **Observation:** pending: Harshkumar Kodavala.
- **Expert interview:** pending: Pal Jalodara. Its output, a corrected criteria list and trusted sources per criterion,
  feeds FR-03 (US-03) and FR-14 (US-16).

## API sign-ups and limits

Recorded on 3 November 2026 in [docs/api-setup.md](../api-setup.md), which names each provider and links its terms and
limit pages. Free tiers change without notice, so that page is the one to keep up to date.

| Service | Free limit recorded | Result on our key |
|---|---|---|
| Language-model service A (private and public text) | 30 requests a minute, 1,000 a day, 8,000 tokens a minute, 200,000 tokens a day | Worked |
| Language-model service B (public text only) | Not published; shown only in the provider's console | Worked |
| Language-model service C (public text only, last choice) | 20 requests a minute; 50 a day until 10 credits are bought, then 1,000 a day | Worked |
| Language-model service D (private and public text) | No free tier that renews: a one-time $5 credit, valid 30 days | Failed: HTTP 402 |
| Language-model service E (public text only) | 188 requests and 625,000 tokens a minute, from the response headers | Worked |
| Web search (Serper) | 2,500 free queries in total, once | Worked: 1 query used |
| Reddit archive (Arctic Shift) | About 1 request a second | Worked |
| Reddit archive (PullPush) | Not known | Failed: HTTP 522, then timeouts |
| Reddit's own API | Access looks closed to new requests | Not set up |
| YouTube Data API | 10,000 units a day; `search.list` limited to 100 calls a day | Worked: 1 unit used |
| Web page reader | 20 requests a minute without a key; 500 with a free key, but the same page also says 100, so plan for 100 | Worked |
| Model hub | Per 5 minutes, signed in: 1,000 API calls, 5,000 file downloads, 200 page requests (table dated September 2025) | Not checked: no token |
| Database (Supabase) | Free plan; pauses after 7 days without use | Worked |

## What this changes in the backlog

The survey, interviews, observation and expert interview cannot change the backlog until their results are in
(pending: the owners above). The API limits already support three points:

| Finding | Evidence | Links |
|---|---|---|
| Private text depends on one working language-model service | Of the two services allowed private text, one allows 8,000 tokens a minute and the other answered HTTP 402 | NFR-04 (US-46) |
| Reddit evidence must come from archives for now | Reddit's own API looks closed to new requests, and one of the two archives failed | FR-11 (US-12) |
| Web searches must be cached | 2,500 free queries in total, once, not each month | FR-11 (US-12) |
