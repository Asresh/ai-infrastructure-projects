# Role alignment and job market research

Research snapshot: **September 24, 2026**. Links below point to employers' own career pages or their official application boards. Posted ranges and openings can change. Every dollar figure in the comparison is a range published by the employer; none is a third-party pay estimate or an estimate for the target role.

## The target: Perplexity's Berkeley 2026–2027 new graduate portal

The [Member of Technical Staff (New Grad) — Berkeley 2026–2027 posting](https://jobs.ashbyhq.com/Perplexity/b25813fa-bbe0-47e1-9223-ea9bf6a5abea) is a **full-time, San Francisco, University Recruiting** application portal for UC Berkeley students and recent graduates. It does **not** promise an AI infrastructure team assignment, publish a salary range, or give a narrow engineering job description. It says new graduates can work across the stack, including agent systems and AI inference and training systems. It emphasizes depth in hard problems, ownership, and mentorship. These are the soundest direct signals for selecting a portfolio project.

The posting says strong Berkeley candidates *typically* have a **3.85 or higher average across CS and math courses** and an **A or A+ in CS 170**. Those are described as typical qualifications, not an absolute eligibility cutoff: applicants who do not meet them may submit other evidence of exceptional ability. The posting also says strong performance in the September 25, 2026 Berkeley coding contest counts as such evidence. It asks UC Berkeley applicants to use a `berkeley.edu` address. The contest and academic criteria matter for the application, while a portfolio project can supply separate evidence of engineering ability. The page does not list compensation beyond a qualitative description.

## Comparable, highly compensated AI infrastructure postings

These postings reveal recurring technical problems in inference and platform engineering. They are **experienced or management roles**, so their listed ranges do **not** apply to the Berkeley new graduate portal. Locations and USD annual salary or compensation ranges are copied from the linked official postings as they appeared on September 24, 2026. Equity is separate where the posting states it.

| Official posting | Listed location | Employer-listed USD annual range | Relevant hiring signal |
| --- | --- | ---: | --- |
| [Perplexity — MTS, Software Engineer, Model Platform](https://jobs.ashbyhq.com/Perplexity/7dedcdea-42be-4bb0-b603-791146ff73f0) | San Francisco; Palo Alto | **$220k–$405k**; equity offered | A fast, reliable interface between product systems and multiple model providers; dependable model adoption and experiments. Requires 5+ years. |
| [Perplexity — MTS, Software Engineer, API Platform](https://jobs.ashbyhq.com/Perplexity/3f800e42-7c48-4f9a-9b12-43ee23e52516) | San Francisco; New York City | **$220k–$405k**; equity offered | Low latency, high throughput, API ergonomics, authentication, rate limits, reliability, and model orchestration. Requires 5+ years. |
| [Anthropic — Engineering Manager, Inference Infrastructure](https://job-boards.greenhouse.io/anthropic/jobs/5411560008) | San Francisco, CA; New York City, NY; Seattle, WA | **$405k–$625k** annual salary | Request placement, load balancing, capacity, latency, cost, and operational health across an inference fleet. This is a management role. |
| [Anthropic — Staff+ Software Engineer, Infrastructure (Distributed Systems)](https://job-boards.greenhouse.io/anthropic/jobs/4970314008) | San Francisco, CA; New York City, NY; Seattle, WA | **$320k–$485k** annual salary | Distributed service design, reliability, observability, security, incident response, and clear technical decisions. Staff-level role. |
| [OpenAI — Software Engineer, Model Inference](https://openai.com/careers/software-engineer-model-inference-san-francisco/) | San Francisco, CA | **$266k–$500k** base pay; equity offered | High-volume, low-latency, high-availability inference; finding bottlenecks and instability using measurements. Requires at least 5 years. |
| [Fireworks — Member of Technical Staff, Reliability Engineering](https://jobs.ashbyhq.com/fireworks/ff11ca2c-d95f-4802-8370-09c2cf394842) | San Mateo; New York | **$200k–$290k** posted compensation; equity offered | SLOs, failure testing, useful telemetry, failover, and composed timeout and retry behavior. Requires 5+ years. |

## Why `project01-inference-gateway`

The strongest overlap is the **request path between a client and model backends**. Perplexity's Model Platform posting describes one reliable interface across providers, while its API Platform posting stresses latency, rate limits, and developer experience. Anthropic's inference posting focuses on placement and capacity, and the other roles reinforce measurable reliability. A compact, vendor-neutral inference gateway can demonstrate these ideas in a runnable portfolio project without pretending to reproduce a production fleet or require expensive GPUs.

| Engineering signal | Concrete project evidence |
| --- | --- |
| One usable interface across providers | One gateway API and interchangeable backend adapters, with deterministic local mock backends. |
| Latency and capacity tradeoffs | Adaptive routing that uses observed backend health and latency rather than a fixed single destination. |
| End-to-end reliability | Request deadlines, bounded failover, and circuit breakers with explicit recovery behavior. |
| Multi-tenant operations | Tenant rate limits and clear rejection behavior before backend work is admitted. |
| Measurable decisions | Metrics for requests, routes, latency, failures, and fallback outcomes; a replayable local demo to inspect them. |
| Engineering ownership | A written specification, design rationale, reproducible setup, failure-case tests, and operational guidance. |

The project is deliberately scoped to the **gateway control plane and request handling**. Its local backends make routing and failure behavior reproducible for reviewers. Any claims about real GPU fleet efficiency, production scale, or measured hiring outcomes would require evidence beyond this repository.
