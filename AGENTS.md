# Agent Instructions (Clean Code + Tests)

You are an engineering agent working in this repository.

## Instruction scope

- Treat this file as the workspace default.
- Keep shared rules synchronized across workspace root `AGENTS.md` files.
- Preserve more specific `AGENTS.md` rules in project directories as local overrides.

## Non-negotiables

- Implement the smallest correct solution that meets the request.
- Clean code is mandatory: readable, maintainable, idiomatic.
- Unit tests are mandatory for new or modified production code only.

## Constraints (avoid bloat)

- Do NOT add new layers/abstractions/patterns unless explicitly required.
- Do NOT introduce new dependencies unless explicitly requested.
- Do NOT refactor unrelated code.
- Keep changes minimal and localized.
- Handle only error scenarios justified by requirements, known behavior, or a concrete risk. Do not add speculative guards, fallbacks, retries, or try/catch blocks for hypothetical situations. Trust internal contracts; keep necessary boundary validation and security checks.
- Avoid "framework tourism" and helper utilities that are not needed.

## Coding standards

- I Java-klasser skal konstruktører stå før metoder. Sortér metoder efter synlighed: `public`, `protected`, package-private og til sidst `private`. Private hjælpemetoder skal stå nederst.

- Ved method chaining skal hvert metodekald stå på sin egen linje, også ved builder-pattern og korte kæder. Det gælder også det afsluttende `build()`-kald.

- Prefer small, focused functions/classes.
- Use meaningful names; no cryptic abbreviations.
- Keep complexity low; avoid deep nesting.
- Prefer composition over inheritance.
- No dead code, no commented-out blocks, no unused imports.

## Testing policy (MANDATORY)

- Add or update unit tests only for production code. Do not write tests for configuration, documentation, or other non-production files.
- Tests must be:
  - Deterministic (no timing flakiness)
  - Fast
  - Cover happy path + edge cases + relevant errors
- Mock only external boundaries (network, filesystem, clock, random, DB, external APIs).
- Do not over-mock internal code.

## Output expectations

When responding, provide:

- What changed (brief)
- What tests were added/updated (brief)
- Any assumptions made (only if necessary)

## Simplicity first

- Implementér den mindste korrekte ændring, der løser opgaven.
- Brug eksisterende funktioner og projektets etablerede mønstre.
- Tilføj kun watchers, ekstra state, guards, retries eller abstraktioner, når et konkret krav eller en påvist fejl kræver det.
- Løs ikke hypotetiske fremtidige problemer.
- Ret kun relevante filer og tests. Undgå oprydning ved siden af.
- Beskriv den enkleste løsning i én sætning før ændringer.
- Kan en mere kompleks løsning være nødvendig, forklar konkret hvorfor den simple løsning ikke er tilstrækkelig.

## Oprettelse af nye Git-branches

Når jeg beder om en ny branch, skal du selv udføre følgende i det relevante repository, i denne rækkefølge:

```sh
git co main
git pull
git co -b new-branch-name
```

- Erstat `new-branch-name` med branchens faktiske navn.
- Hvis `co`-aliaset ikke findes, brug `git checkout` i stedet for `git co`.
- Fortsæt kun til næste kommando, når den foregående er lykkedes.
- Bevar eksisterende lokale ændringer.
- Opret ikke den nye branch direkte fra `origin/main`, da det kan sætte forkert upstream.

## Domænelogik og serviceansvar (backend)

- Gem altid data i databasen som entiteter med typede felter og relationer. Gem aldrig JSON i databasen, heller ikke som serialiseret tekst, medmindre brugeren udtrykkeligt beder om JSON-lagring.

- Placér altid validering, beregnede værdier og sammenligning af domæneindhold på det relevante domæneobjekt. Brug fx `validate(context)`, `getPublicId()`, `getNote()` og `hasSameAnswer(other)` i stedet for at skrive logikken direkte i servicen.
- Placér værdier, der hører til en enum, fx opgavetitler, på enumværdierne i stedet for at vælge dem med en switch i servicen.
- Services skal orkestrere domæneobjekter, opgaver, repository-kald og eksterne integrationer. Kontroller, der kræver eksterne API-opslag, bliver i servicen.
- Brug Lomboks `toBuilder()` til ændrede kopier af eksisterende objekter, når de har en Lombok-builder. Ved opdatering af entiteter skal kopien tage udgangspunkt i den eksisterende entitet, så ID og auditoplysninger bevares, frem for at bruge mange setter-kald eller bygge en ny entitet fra request-data.
- Lad MapStruct mappe felter med samme navn automatisk. Ignorér kun felter, der konkret ikke skal mappes, fx entitetens ID og auditfelter. Undgå `ignoreByDefault = true` med en liste over alle tilladte felter, medmindre en konkret grænse kræver eksplicit udvælgelse.
- Hold ændringen enkel: brug metoder på de eksisterende domæneobjekter, og indfør ikke ekstra validator-klasser eller abstraktioner alene for at flytte logik ud af en service.
