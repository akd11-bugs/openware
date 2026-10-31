# Architecture

Draft: the product is not yet defined, so the diagrams below are a generic
placeholder structure. Replace component names as decisions are made, and record
significant revisions in [changes.md](changes.md).

Diagrams use [Mermaid](https://mermaid.js.org/), which GitHub and VS Code render.

## System architecture

```mermaid
flowchart LR
    User([User]) --> Client[Client / UI]
    Client -->|requests| API[API / Core service]
    API --> Logic[Business logic]
    Logic --> Store[(Data store)]
    Logic --> Ext[External services]
```

| Component | Responsibility | Status |
| --------- | -------------- | ------ |
| Client / UI | User-facing interface | TBD |
| API / Core service | Entry point, validation, auth | TBD |
| Business logic | Core domain behavior | TBD |
| Data store | Persistence | TBD |
| External services | Third-party integrations | TBD |

## User flow

```mermaid
flowchart TD
    A[Open app] --> B{Signed in?}
    B -- No --> C[Sign up / log in]
    C --> D[Onboarding]
    B -- Yes --> E[Main view]
    D --> E
    E --> F[Perform core action]
    F --> G{Success?}
    G -- Yes --> H[Show result]
    G -- No --> I[Show error and retry]
    I --> F
    H --> E
```

## Request sequence (core action)

```mermaid
sequenceDiagram
    actor U as User
    participant C as Client
    participant A as API
    participant D as Data store
    U->>C: Trigger action
    C->>A: Request
    A->>A: Validate / authorize
    A->>D: Read / write
    D-->>A: Result
    A-->>C: Response
    C-->>U: Updated view
```

## Decisions and constraints

- _TODO: stack, hosting, auth approach, data model_

## Open questions

- _TODO_
