# Lab 6, section 5: Elicitation results

This text replaces the earlier "Next: validate" lines in section 5. Items marked "pending" are completed by the named
owner before submission, using numbers from the counting script and the anonymised notes.

## 5.1 Techniques applied

| Technique | Target | Responsible | Status |
|---|---|---|---|
| Survey | 30 or more responses | Jhanvi Barot | pending: Jhanvi Barot |
| Shopper interviews | 3 interviews of about 20 minutes | Om Pandya, Rushiraj Navar, Jenish Kavad | pending: Om Pandya, Rushiraj Navar, Jenish Kavad |
| Observation | 1 real purchase, thinking aloud | Harshkumar Kodavala | pending: Harshkumar Kodavala |
| Expert interview | 1 laptop or phone expert | Pal Jalodara | pending: Pal Jalodara |
| External service review | Every outside service | Jenish Kavad | Completed on 3 November 2026 |

Survey respondents are counted and never named, interviewees are identified as P1, P2 and P3, and the expert is
identified only by field.

## 5.2 Survey results

Number of responses: pending: Jhanvi Barot.

Questions 4 to 7 test specific design decisions: interview length (FR-05), citations (FR-18, NFR-14), memory (FR-06) and
ties (FR-16). Results: pending: Jhanvi Barot.

## 5.3 Shopper interviews

pending: Om Pandya, Rushiraj Navar, Jenish Kavad.

## 5.4 Observation

pending: Harshkumar Kodavala.

## 5.5 Expert interview

pending: Pal Jalodara.

## 5.6 External service limits

Every outside service was reviewed on 3 November 2026 for its free limits, its terms and whether it worked with our key.
The main results were as follows.

- The primary language-model service allows 30 requests a minute, 1,000 a day, 8,000 tokens a minute and 200,000 tokens
  a day.
- A second language-model service allowed to process private text offers no free tier that renews, and answered HTTP 402.
- The web search service allows 2,500 free queries in total, once.
- The YouTube Data API allows 10,000 units a day.
- Reddit's own API appears closed to new requests. Of the two archives tested, one worked at about 1 request a second
  and one failed.

## 5.7 Findings

**Finding:** Private text depends on one working language-model service.
**Evidence:** Of the two services allowed private text, one allows 8,000 tokens a minute and the other answered HTTP 402
on 3 November 2026.
**Requirements:** NFR-04.

**Finding:** Reddit evidence must come from archives for now.
**Evidence:** Reddit's own API appears closed to new requests, and one of the two archives failed on 3 November 2026.
**Requirements:** FR-11.

**Finding:** Web searches must be cached.
**Evidence:** The web search service gives 2,500 free queries in total, once, not each month.
**Requirements:** FR-11.

Findings from the survey, interviews, observation and expert interview: pending: Jhanvi Barot, Om Pandya, Rushiraj
Navar, Jenish Kavad, Harshkumar Kodavala, Pal Jalodara.
