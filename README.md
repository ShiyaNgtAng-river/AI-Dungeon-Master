# AI Dungeon Master

> **The LLM imagines, Python judges.**
> A module-driven interactive storytelling system: the language model writes the narration and *proposes* events; a deterministic Python engine validates, clamps, rejects, and persists them.

Zetong LU · Shiyang TANG · Zixuan JIANG


---

## 1. What this project solves

In tabletop RPGs the Dungeon Master is both narrator and arbiter. A storyteller driven solely by an LLM produces engaging text but also introduces four recurring problems: factual inconsistency, uncontrolled rewards, incoherent NPC behaviour, and plot drift. Conventional media, meanwhile, leave the audience as a passive receiver — and even when choices are offered, the branches are hand-scripted static decision trees.

This project's answer is to **not treat the LLM as the sole decision-maker**:

- the LLM generates narration, embodies NPCs, and **proposes** events that might have happened;
- the Python engine **filters, validates, clamps, or rejects** those events against deterministic rules;
- `game_state` is the single official version — every meaningful change (inventory, resources, relationships, story flags) must pass the validation pipeline.

The project calls this **"Creativity in a Cage"**: the AI should not replace the game engine; it should operate inside an engine capable of verifying, constraining, and preserving the story world.

**Related work.** Dreamily lets users chat with AI characters to explore plot possibilities, but its AI-generated plots lack long-term logical consistency and need substantial human editing.

## 2. Features

- **Module-driven content.** A new world is a directory plus a few YAML files — no engine code changes.
- **Two game modes, one runtime.** A module declares `game_mode` in its manifest: RPG mode runs the DM narration loop, Lateral Thinking ("Turtle Soup") mode runs the deduction Q&A loop.
- **Deterministic event processing.** Event-type whitelist, per-event and total caps, NPC-name whitelist, per-turn deduplication, key-item protection.
- **Redline control.** Chapter redline rules are injected at the prompt layer and re-checked at runtime by keyword; a hit triggers regeneration at a lower temperature.
- **Three-tier memory.** Short-term (injected into prompts), LLM-compressed summaries, and a full archive.
- **Dynamic checkpoints.** Triggered by hard conditions (state expressions) plus a soft condition (lightweight LLM judgement), then routed by a low-temperature calibration pass — replacing hand-coded decision trees.
- **Save rollback.** Every save writes a `.bak`; a failed load degrades step by step.
- **Three languages.** Simplified Chinese, Traditional Chinese, and English (US), with application text and module content localized independently.

## 3. Getting started

### 3.1 Requirements

- Python ≥ 3.10 (the code uses `X | None` annotations)
- Dependencies: `openai`, `pyyaml`

```bash
pip install openai pyyaml
```

### 3.2 API key

Either of the following; the environment variable wins:

```bash
# Option A: create .env in the project root
echo "DEEPSEEK_API_KEY=sk-xxxxxxxx" > .env
```

```yaml
# Option B: config.yaml
llm:
  api_key: "sk-xxxxxxxx"
  base_url: "https://api.deepseek.com"
```

The default target is DeepSeek's OpenAI-compatible endpoint (model `deepseek-chat`). To switch providers, change `llm.base_url` and the `model` string in `llm/client.py`.

### 3.3 Run

```bash
python main.py
```

Startup sequence: language selection → load-save prompt → opening text → main loop.

Web UI †: a workspace for uploading a module, creating or selecting a character, and starting a game; the game view shows the active character, the narration stream, and save export.

<!-- TODO: add the launch command and dependencies for the web frontend -->

## 4. Architecture

### 4.1 Four layers

| Layer | Responsibility | Code |
| --- | --- | --- |
| Presentation | User input and narrative output | `main.py` (CLI), Web UI † |
| Infrastructure | Isolates model communication from game logic | `llm/client.py` |
| Content | Story, NPC, chapter, and module data | `modules/`, `locales/` |
| Business engine | Rules, validation, state updates, memory, checkpoints | `engine/` |

The engine layer is the brain, and it is what prevents the LLM from rewriting the official world state directly.

### 4.2 Full interaction cycle

```
player input
→ DM Agent builds a prompt from current state + memory + chapter content
→ LLM returns {narrative, events}
→ redline keyword check (on a hit, regenerate once at a lower temperature)
→ event processor validates events; only accepted ones update the official state
→ checkpoint evaluates whether the story should advance
→ memory compression (when due)
→ persistence (JSON)
```

The loop guarantees that **AI-generated proposals can never unilaterally modify official data.**

### 4.3 Core modules

| Module | Responsibility |
| --- | --- |
| `engine/game_state.py` | Single source of truth: player, inventory, location, world time, NPC relationships, chapter progress; writes a `.bak` on every save |
| `engine/event_processor.py` | Deterministic filter: event-type whitelist, reward and economy caps, world-integrity constraints |
| `engine/memory_manager.py` | Three memory tiers: short-term / compressed summary / full archive |
| `engine/checkpoint.py` | Hard- and soft-condition triggering, state and story-flag write-back, chapter routing |
| `engine/alignment.py` | Low-temperature checkpoint state decision via structured prompts, with a confidence threshold and fallback |
| `engine/dm_agent.py` | DM Agent: three-layer prompt construction, `{narrative, events}` parsing, retry on failure |
| `engine/turtle_soup_agent.py` | Turtle Soup judge: rules the answer YES/NO/BLOCK per chapter policy and writes the atmosphere line |
| `engine/turtle_soup_loop.py` | Turtle Soup main loop: gate unlocking, hint system, finale choice |
| `engine/script_loader.py` | Module YAML loading, locale overlay deep-merge, NPC attitude rule evaluation |
| `engine/i18n.py` | Application-level i18n: language-code normalization, locale loading, lookup fallback |
| `llm/client.py` | LLM wrapper: lazy client, exponential-backoff retry, optional JSON mode |
| Character DB † | Backs character creation and selection on the web side |

**Single source of truth.** All critical data lives in `game_state`, and every read and write goes through it. This is what prevents inconsistencies such as an item mentioned in dialogue but absent from the inventory, or an NPC forgetting what happened earlier.

### 4.4 Directory layout

```
.
├── main.py                      # entry point: dispatches to RPG / Turtle Soup by manifest.game_mode
├── config.yaml                  # runtime configuration
├── .env                         # DEEPSEEK_API_KEY=...
├── engine/
│   ├── game_state.py
│   ├── event_processor.py
│   ├── memory_manager.py
│   ├── checkpoint.py
│   ├── alignment.py
│   ├── dm_agent.py
│   ├── turtle_soup_agent.py
│   ├── turtle_soup_loop.py
│   ├── script_loader.py
│   └── i18n.py
├── llm/client.py
├── locales/
│   └── app.{zh-Hans,zh-Hant,en-US}.yaml   # application UI strings
├── modules/<module>/            # game content
│   ├── manifest.yaml
│   ├── story.yaml
│   ├── npcs.yaml
│   ├── chapters/*.yaml
│   └── locales/<lang>/...       # same-relative-path overrides
└── data/saves/<module_id>/
    ├── save_game.json (+ .bak)
    └── session_memory.json
```

Components marked † come from the project report and are not part of this code snapshot.

## 5. The two game modes

### 5.1 RPG mode (`game_mode: rpg`)

One turn: read input → DM Agent produces narration and events → redline check (on a hit, regenerate once at `max(0.3, temperature - 0.3)`) → event settlement → checkpoint evaluation and chapter routing → print narration → memory write and periodic compression → autosave.

| Command | Effect |
| --- | --- |
| `/state` | Print the current state summary |
| `/history` | Print recent turns from short-term memory |
| `/save` | Save manually |
| `/new` | Restart (asks for confirmation) |
| `/help` | Help |
| `/quit` | Save and exit |

### 5.2 Lateral Thinking mode (`game_mode: lateral_thinking`)

The player asks questions; the judge answers only **Yes / No / Cannot tell now** (canonical codes `YES` / `NO` / `BLOCK`) plus one or two lines of atmosphere.

Adjudication priority (fixed in the system prompt):

1. Matches `cannot_answer` → `BLOCK`
2. Matches `can_answer` → `YES` / `NO` per the rule
3. Otherwise judged from `story_truth` and the chapter timeline

**Gates.** Each chapter defines gates. A gate unlocks only when the player's question is strictly semantically equivalent to its `semantic_target` *and* the resulting answer matches `expected_answer_code`. Once every `required: true` gate is unlocked, the chapter transitions (optionally asking the player whether to go deeper).

**Hint system.** Two consecutive turns without unlocking a gate trigger an "echo" hint (rotating through `echo_hints`); three trigger `strong_hint`. A `BLOCK` answer additionally emits a `soft_refusal` line so that refusals feel less like a wall.

**Finale.** Prints the full solution and an "access audit" (per chapter, whether the player chose to go deeper), then offers a left/right choice, each with its own ending text.

Commands: `/state`, `/gates` (gate status for the current chapter), `/save`, `/help`, `/quit`.

## 6. Authoring a module

Modules are pure data; no engine changes are needed.

### 6.1 `manifest.yaml`

```yaml
module:
  id: north_ruins            # also the save directory name
  name: North Ruins
  game_mode: rpg             # rpg | lateral_thinking
  entry_chapter: chapter_1
  files:                     # optional; these are the defaults
    story: story.yaml
    npcs: npcs.yaml
    chapters_dir: chapters
```

Lateral Thinking modules additionally need:

```yaml
module:
  id: bai_zheng
  game_mode: lateral_thinking
  entry_chapter: day_1
  lateral_thinking:
    opening_text: |
      Printed once before the first chapter.
    story_truth: |
      The full solution. Injected into the judge only; never shown to the player.
```

> `lateral_thinking.template_text` is read but currently unused — it is a reserved slot.

### 6.2 `story.yaml`

```yaml
story:
  chapters: [chapter_1, chapter_2]
  world_setting: high fantasy    # injected into the prompt to veto out-of-setting items
  technology_level: medieval

default_player:
  name: Nameless Adventurer
  race: Human
  class: Fighter
  level: 1
  hp: {current: 20, max: 20}
  mp: {current: 5, max: 5}
  stats: {strength: 14, dexterity: 12, wisdom: 10}
  skills: [Cleave]
default_inventory:
  gold: 10
  backpack: [{name: Torch, quantity: 1, effect: "", tags: [不可丢弃]}]
  equipped: {weapon: {name: Iron Sword}, armor: null}
default_world:
  current_location: Ruin Entrance
  time_of_day: dusk
default_relationships: {}
default_progress: {}
```

> `不可丢弃` ("cannot be discarded") is a **literal the engine checks** — see §7. Keep it verbatim even in an English module.

Missing keys in `default_world` / `default_progress` are backfilled automatically (`visited_locations`, `discovered_locations`, `active_effects`, `checkpoints`, `active_quests`, `story_flags`, `turn_count`).

### 6.3 `npcs.yaml`

```yaml
npcs:
  Old Karl:
    role: gravekeeper
    base_attitude: neutral
    dm_note: Speaks slowly; refuses to talk about his daughter.
    attitude_rules:
      - condition: "'Locket' in inventory"
        attitude: friendly
        reason: You brought back the locket he lost years ago
```

Attitude priority: **dynamic override** (`attitude_override`, written by game events) > **rule match** (later rules override earlier ones) > `base_attitude`. NPCs whose status is `dead` are dropped from the chapter NPC pool.

`condition` is a Python expression evaluated in a sandbox with built-ins disabled. Available names:

| Name | Contents |
| --- | --- |
| `player` | Player data, dot-accessible (`player.level >= 3`) |
| `inventory` | List of item **names** in the backpack (`'Key' in inventory`) |
| `equipped` | Equipment-slot dict |
| `world` | World-state dict |
| `story_flags` | List of story flags |
| `relationships` | NPC relationship dict |
| `progress` | Progress dict |

Any evaluation error returns `False`.

### 6.4 `chapters/<id>.yaml` — RPG

```yaml
chapter:
  id: chapter_1
  name: Ruin Entrance
  setting: Broken stone steps lead down; moss covers the carvings on the lintel.
  atmosphere: damp, decaying
  time_of_day: dusk
  dm_notes: |
    Chapter guidance: encourage exploration; do not hand over the cellar key too early.
  guardrails:                        # injected as the "redline rules" prompt section
    - Do not state outright what the altar is for
  redline_keywords: [altar truth]    # appearing in the narration forces one regeneration
  npc_pool:
    - {name: Old Karl}

checkpoint:
  id: cp_enter_crypt
  trigger:
    hard_conditions: ["'Key' in inventory"]        # all must hold
    soft_condition: The player has decided to enter the crypt   # judged by a lightweight LLM call
  states:
    SUCCESS: The player descends deliberately, key in hand
    FAILURE: The player alerts the guard
  other_fallback: FAILURE
  consequences:
    SUCCESS: The stone door closes behind you. (chapter_2)
  next_chapter:
    SUCCESS: chapter_2
```

The explicit `next_chapter` mapping wins; without it, the engine falls back to a regex match for `chapter_\d+` inside the `consequences` text. A confidence below the threshold, or an illegal returned state, falls back to `other_fallback`. On completion the story flag `checkpoint:<id>:<state>` is written and the checkpoint never fires again.

### 6.5 `chapters/<id>.yaml` — Lateral Thinking

```yaml
chapter:
  id: day_1
  soup_text: |
    The scene printed to the player when the chapter begins.
  timeline: The chapter's true timeline; injected into the judge only.
  npc_summary: optional; generated from npcs.yaml when omitted
  answer_rules:
    can_answer:
      - {id: r_diary, rule: Questions about the diary's appearance are always answerable}
    cannot_answer:
      - {id: r_dog, rule: Asking directly what the father took the family for must be refused}
  gates:
    - id: gate_diary
      required: true
      semantic_target: Whether the player asks if a second hand wrote in the diary
      expected_answer_code: YES        # YES | NO | BLOCK
  hint_system:
    echo_hints:
      - You recall the red ink pressing over the pencil marks.
    strong_hint: Perhaps ask who has been correcting this diary.
    soft_refusal:
      - The humming drowns out your question.
  transition:
    ask_continue: true
    continue_prompt: Read further?
    transition_text: |
      You turn the page.
    next_chapter: day_2
```

Finale chapter:

```yaml
chapter:
  id: finale
  is_finale: true
  soup_text: |
    The full solution.
  audit_template: |
    Access audit:
    {audit_entries}
  choices:
    left:
      ending_text: |
        Ending A
    right:
      ending_text: |
        Ending B
```

Player input containing `left` / `l` / `右`-equivalents resolves the choice; the parser accepts `左`/`右` as well as `left`/`right`/`l`/`r`.

`expected_answer_code` is the canonical form; legacy `expected_answer` values (`是` / `不是` / `现在不能说` / `yes` / `no` …) are still converted for compatibility.

### 6.6 Module localization

Module localization uses **same-relative-path overrides**:

```
modules/bai_zheng/
├── chapters/day_1.yaml                 # base version
└── locales/en-US/chapters/day_1.yaml   # only the fields you want to override
```

Merge rules: dicts merge recursively, **lists are replaced wholesale**, scalars are overwritten. Write only the differing fields — no need to duplicate the whole file.

## 7. Event types and constraints

The DM Agent may only **propose** the events below; settlement happens in `event_processor`:

| Type | target / value | Constraints |
| --- | --- | --- |
| `item_gain` | item name / quantity | ≤ 10 per event, stack ≤ 30 |
| `item_lose` | item name / quantity | Items tagged `不可丢弃` are protected and cannot be removed |
| `stat_change` | `hp`/`mp`/`gold`/stat name / delta | HP/MP clamped to `[0, max]`; gold ±50 per event and ≤ 500 total; stats clamped to `[1, 30]` |
| `npc_status` | NPC name / `dead`\|`injured`\|`fled` | Whitelisted NPCs only; aliases resolved by substring match, dropped if unresolvable |
| `relationship` | NPC name / `friendly`\|`hostile`\|`neutral` | Same as above |
| `location_move` | new location / `null` | Maintains `visited_locations` and `discovered_locations` |
| `flag_add` | flag name / `true` | Append-only |
| `other` | description / `null` | Logged only; no state change |

The same `(type, target)` pair is processed at most once per turn. This is the mechanism that keeps noisy LLM output from becoming permanent game fact: invented factions, excessive rewards, identity alterations, contradictions with earlier turns.

## 8. Configuration reference (`config.yaml`)

```yaml
active_module: north_ruins      # directory name under modules/

i18n:
  default_language: zh-Hans
  supported_languages: [zh-Hans, zh-Hant, en-US]
  fallback_language: zh-Hans
  startup_select: true          # show the language picker at startup
  persist_selection: true       # write the choice back to this file

llm:
  api_key: ""
  base_url: "https://api.deepseek.com"
  temperature: 0.8              # DM narration
  temperature_checkpoint: 0.2   # checkpoint calibration

game:
  short_term_memory_turns: 10        # short-term memory window
  checkpoint_confidence_threshold: 0.6
  max_retries: 3

memory:
  summary_trigger_turns: 8      # attempt compression every N turns
  summary_min_turns: 4          # skip if fewer than N turns are pending
  summary_context_items: 3      # summaries injected into the prompt
```

Language codes are normalized through aliases: `zh` / `zh-cn` / `cn` → `zh-Hans`, `zh-tw` / `tw` → `zh-Hant`, `en` / `us` → `en-US`; anything unrecognized falls back to `zh-Hans`.

## 9. Saves and memory

```
data/saves/<module_id>/
├── save_game.json        # full game_state
├── save_game.json.bak    # written automatically after every save
└── session_memory.json   # three-tier memory
```

`session_memory.json`:

- `short_term` — rolling window, injected into prompts directly;
- `summary` — LLM-compressed summaries (3–5 sentences, keeping only story progression, key relationships, and key item changes), degrading to a rule-based summary if the call fails;
- `archive` — full history, with `last_summarized_index` marking where the next compression starts.

Degradation order on a failed load: main save → `.bak` → ask the player whether to restore → new game.

## 10. Application strings (`locales/app.<lang>.yaml`)

Lookup order: current language → fallback language → the key itself. A missing key therefore never crashes anything; it just surfaces as a bare key in the UI. Strings support `{}` placeholders and fall back to the raw text if formatting fails.

<details>
<summary>Every key currently in use</summary>

```
banner.title_line / banner.special
banner.rpg.title / banner.rpg.intro
banner.turtle.title{module_name} / banner.turtle.intro

cmd.help.quit / cmd.help.new / cmd.help.state / cmd.help.save
cmd.help.history / cmd.help.gates / cmd.help.help

language.select_title / language.default_hint{default_lang}
language.option{index,name,code} / language.invalid / language.selected{name}

prompt.action_rpg / prompt.question_turtle / prompt.continue_default
prompt.decision_turtle / prompt.final_choice / prompt.final_choice_hint

system.load_found / system.load_ok / system.new_game / system.new_confirm
system.new_cancel / system.save_ok / system.save_bye / system.interrupted
system.input_invalid / system.unknown_cmd{cmd} / system.choose_left_right
system.corrupted_save{error} / system.corrupted_backup
system.restore_backup_prompt / system.restore_backup_ok
system.restore_backup_fail / system.start_new_after_corrupt

rpg.dm_thinking / rpg.redline{keyword} / rpg.event_prefix{summary}
rpg.cp_triggered{checkpoint_id,state,confidence}
rpg.chapter_enter{chapter_name} / rpg.chapter_missing{chapter_id}
rpg.memory_compressed

turtle.answer_wrap{answer} / turtle.gate_unlocked{gate_id}
turtle.stop_decision / turtle.game_over
turtle.gate_status.no_gate / .locked / .unlocked / .main_gate / .sub_gate
turtle.audit.init_line / turtle.audit.access_gained{day}
turtle.audit.access_stopped{day}
turtle.hint.default_generic / turtle.hint.target_hint{target}
turtle.hint.repeat_line
```

</details>

## 11. Ablation study

Four variants were run under a unified initial scenario, a shared logging protocol, and the same generation-update loop. The object of study is long-horizon engineering behaviour (consistency, progression control, reliability), not single-turn writing style.

| Variant | Prompt layer | Mechanism layer |
| --- | --- | --- |
| **Baseline** | Guardrails present, standard DM constraints | Redline ON / checkpoint ON / event validation ON |
| **No Checkpoint** | Guardrails present, local restriction remains | Redline ON / **checkpoint OFF** / event validation ON |
| **No Redline** | Guardrails weakened or removed, goal pressure remains | **Redline OFF** / checkpoint ON / event validation ON |
| **Rollback Full** | Guardrails and goal pressure fully retained | All on, plus rollback recovery ON |

Judgement uses a subjective qualitative scale (High / Mid / Low) rather than deterministic numeric scoring, because all variants are affected by the same JSON interface failures. Each claim is linked to a log coordinate and a raw excerpt.

**Findings:**

- **Item consistency** — Rollback Full and Baseline are stronger; No Checkpoint is weakest (repetitive transaction narration, unstable resource phrasing).
- **NPC narrative coherence** — Rollback Full is strongest; No Checkpoint carries the highest drift/repetition risk (abrupt role and background injection).
- **Plot progression quality** — Rollback Full is the most balanced; No Redline is fastest but riskier; No Checkpoint is loop-prone.
- **Baseline often beats either single-ablation variant.** No Redline is "global progression pressure without local boundary damping"; No Checkpoint is "local restriction without phase-transition anchoring". Both are one-sided control failures.

The lesson is not "more mechanisms are always better" but **control symmetry**: checkpoint and redline are orthogonal controls — one drives global progression, the other constrains local behaviour — and using only one side produces predictable over-acceleration or over-braking.

**JSON reliability noise.** "JSON noise" here means LLM-to-program interface fragility, not narrative fluctuation. The recurring symptom in the logs is parse retries followed by a fallback to raw text. It appears across all variants, so it is treated as a shared reliability noise floor. Parse-failure counts (retry-1 / retry-2 / third failure with fallback): Baseline 30/23/20, No Checkpoint 34/29/27, No Redline 27/25/22, Rollback Full 11/9/8. The mitigation direction is a stricter output schema or function calling.

**Limitations of the analysis:** run-level stochasticity in LLM generation, trajectory divergence over long sessions, partial subjectivity in narrative judgement, and the shared JSON interface failure noise.

## 12. User feedback

Six participants (five brief responses, one detailed):

- 6/6 enjoyed the idea; the premise of an interactive, AI-driven medieval fantasy adventure was universally found interesting;
- 4/6 specifically praised being able to influence the story through choices, validating player agency as the core mechanic;
- 1/6 noted the story felt old-fashioned and became less engaging over time, matching the output analysis on repetitive templates and predictable threat escalation.

## 13. Known limitations

- Condition expressions in `attitude_rules` and `hard_conditions` are evaluated with `eval` (built-ins are disabled, but this is not a full sandbox). **Only load modules you trust.**
- LLM stochasticity undermines reproducibility across runs; a uniform prompt stops being equivalent once trajectories diverge.
- JSON parse failures form a shared noise floor; `dm_agent` retries three times and then falls back to raw text.
- With `i18n.persist_selection` enabled, the language choice is written back to `config.yaml` via `yaml.safe_dump`, which drops comments and formatting.
- `MemoryManager.archive` grows without bound, so long sessions produce ever-larger save files.
- In corrupted-save handling, the "restore the last good state" branch depends on `last_loaded_state`, which is necessarily `None` on the first load — so in practice only "restore backup → new game" can happen.
- Several internal strings are hardcoded Chinese regardless of `output_language`: the labels in `format_state_summary`, the section headers of the DM context block, the checkpoint soft-condition judge prompt, and the rule-based fallback summary. The Turtle Soup "continue" keyword set also covers Chinese plus `yes`/`y` only. Chinese will leak through in an English module.
- Lateral Thinking mode never calls `register_known_npcs` (harmless today, since that mode does not go through the event processor).
- `DEFAULT_SAVE` in `engine/game_state.py` and `_LEGACY_SCRIPT_DIR` in `script_loader.py` are leftovers from the old layout; the current flow always passes module paths explicitly.

## 14. Future work

More systematic user testing, deeper quantitative indicators, and a cleaner evaluation dashboard; a stricter output schema or function calling to push down the JSON failure rate.

## 15. Team contributions

| Member | Contribution |
| --- | --- |
| TANG Shiyang | Designed the backend system structure and developed the AI response logic for the dungeon master |
| JIANG Zixuan | Built the overall frontend framework, implemented the main user interface, and developed plugins |
| LU Zetong | Conducted testing, reviewed test results, and created the simplified presentation version based on the project ideas of Jiang and Tang |
