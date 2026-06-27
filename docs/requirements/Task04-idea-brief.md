# NEURAL SYNC — Neural Operator–Project Alignment System

> «Night City не награждает дрейф. Он награждает выравнивание.
> Ни один оператор, ни один дев, ни один актив не должен оказаться не на той войне.»

Идейный бриф проекта (Task04). Самодостаточный документ — для команды, жюри и для входа в pipeline.

---

## 1. Миссия

Построить AI-систему нейро-аллокации, которая непрерывно выравнивает операторов
(девелоперов) и проекты — **не по навыкам, а по совокупности сигналов**: технике,
стилю работы, мотивации, доступности и траектории роста.

Система не распределяет людей — она **объясняет, почему** распределение верно, и
**предупреждает разрушение команды** до того, как оно случится.

Предсказывает: bench-risk, burnout-risk, вероятность рассогласования команды.
Рекомендует: переходы между проектами, пути роста навыков, внутреннюю мобильность.

---

## 2. Архитектурный принцип (ядро решения)

Строгое разделение двух слоёв *(источник: JobMatchAI, arXiv 2603.14558)*:

1. **Детерминированное ядро матчинга** — считает score чистым кодом,
   воспроизводимо и auditable. Никаких LLM в подсчёте.
2. **LLM-слой объяснения** — получает **только** предвычисленные скоры и
   структурные факты (не сырые поведенческие векторы), генерирует «почему этот
   матч», риски и пути роста.

LLM-провайдер — деталь слоя объяснения (Claude / Gemini взаимозаменяемы через
артефакт-промт, без изменения кода). **Матчинг от провайдера не зависит.**

---

## 3. Ядро матчинга

```
MATCH_SCORE = w1·skill + w2·workstyle + w3·motivation + w4·timezone + w5·growth
```

| Измерение | Вес | Метод |
|-----------|-----|-------|
| **skill** | 0.30 | **Гибрид**: пересечение множеств + семантическая близость эмбеддингов (`react ≈ react.js`, `ml ≈ machine learning`), усиление по `experience_years` |
| **workstyle** | 0.25 | косинус поведенческих векторов (async/sync, team/individual, structure/flex) |
| **motivation** | 0.20 | косинус мотивационных векторов (learning/stability/innovation) |
| **timezone** | 0.15 | overlap-окно с затуханием по расстоянию часовых поясов |
| **growth** | 0.10 | соответствие `career_goals` ↔ `growth_opportunities` |

Веса конфигурируемы через admin-панель (Σwᵢ = 1.0).

**Retrieval для масштаба:** перед скорингом — ANN-отбор кандидатов через
векторный индекс (pgvector HNSW): top-K быстро → детерминированный rescore точно.
Масштаб 10k+ операторов.

---

## 4. Слой объяснения (LLM)

Генерирует: `explanation` (почему матч работает), `risks` (timezone-конфликт,
skill-gap, расхождение стилей), `growth_potential`. Промпт — версионируемый
артефакт. **Поведенческие векторы в промпт НЕ передаются** (privacy + audit).

---

## 5. Двигатель ре-оптимизации (не статичная аллокация!)

- **Bench-prediction** — риск простоя по датам окончания проектов.
- **Burnout-detection** — `min(1, (weeks/48)·intensity·(1−motivation_alignment))`;
  over-allocation × интенсивность × рассогласование мотивации.
- **Reallocation** — при `burnout > 0.6` / `bench > 0.7` система **предлагает**
  перестановки через тот же matching-движок (внутренние переходы, skill-bridge
  проекты). Человек в петле подтверждает.

---

## 6. Данные

Identity Layer (`DeveloperProfile`) + Project Genome (`ProjectProfile`).
ETL из HR / Git / Jira / Slack → векторизация → PostgreSQL + pgvector.

---

## 7. Фронт (3 роли)

- **Developer** — рекомендованные проекты, объяснения матчей, пути роста.
- **Manager** — здоровье состава, риск-алёрты, предложения аллокации
  (только бейджи риска, **без** сырых поведенческих векторов).
- **Admin** — тюнинг весов, оверрайды.

---

## 8. Тех-стек

Backend: Python / FastAPI · LLM: configurable (Gemini Phase-1, Claude alt) ·
Vector: pgvector (HNSW) · Embeddings: sentence-transformers · Frontend: React.

---

## 9. Non-functional

- **GDPR**: каскадное стирание + audit-log; поведенческие векторы не утекают в API/UI.
- **Explainable AI**: каждый компонент score инспектируется.
- **Латентность** < 500ms на матч (LLM-объяснение — async, вне SLA).
- **Масштаб** 10k+ операторов.

---

## 10. MVP (Phase 1)

Ингест профилей → гибридный matching-движок → LLM-объяснение → дашборд +
детект bench/burnout с предложением реаллокации.

---

## 11. Условия провала (система считается сломанной, если)

- Матчинг только по навыкам, без поведенческого слоя.
- Нет объяснимости — если система не может сказать **ПОЧЕМУ** матч существует.
- Статичная аллокация без ре-оптимизации.
- Девелоперы отвергают рекомендации > 50%.

---

## 12. Киберпанк-директива (культура команды)

Это не HR-софт. Это нейро-система аллокации во враждебной экономике.
Каждая фича отвечает на вопрос: *«Повышает ли это human-system alignment?»*
Сопротивляйся «просто заматчим по скиллам» — это капитуляция.
Приоритет: извлечение сигнала над UI-полиш, интеллект над автоматизацией.

---

## 13. Deliverables

Реализация модулей · API-контракты · Артефакты-промты LLM ·
Тест-сценарии (хороший матч vs плохой матч).

---

## 14. References

**Эталонные продукты** (что переизобретаем; чем отличаемся):

- **Gloat** — internal talent marketplace, AI skills-ontology. Слабость (Gartner/G2): онтология чисто AI-генерируемая, без поведенческого слоя — **наш дифференциатор**. https://fuel50.com/blog/top-gloat-talent-marketplace-alternatives/
- **Eightfold AI** — deep-learning вывод навыков из данных, trajectory prediction. https://eightfold.ai/
- **Fuel50** — career-pathing «от цели сотрудника назад к возможностям». https://fuel50.com/products/talent-marketplace/
- **Kantata** — PSA resource-optimization, детект skill-gap/конфликтов.

**Эталонные архитектуры** (как строить ядро):

- **JobMatchAI (arXiv 2603.14558)** — источник нашего принципа: строгое разделение детерминированного scoring и LLM-объяснения; LLM получает только предвычисленные скоры + пути по skill-графу → auditable, не галлюцинирует. https://arxiv.org/pdf/2603.14558
- **Smart-Hiring (arXiv 2511.02537)** — CV → shared vector space, cosine по эмбеддингам навыков (`all-MiniLM-L6-v2`), визуализация rationale. https://arxiv.org/html/2511.02537v1
- **Synapse (arXiv 2604.02539)** — two-phase retrieval + LLM, explainable fit. https://arxiv.org/pdf/2604.02539
- **SOO Group** — semantic talent matching: vector similarity → LLM на soft-skills. https://thesoogroup.com/blog/semantic-talent-matching-vector-search

**Open-source строительные блоки:**

- **Gorse** — recommender-движок, авто-обучение. https://github.com/gorse-io/gorse
- **huynhnhathao/job_recommender** — graph + Personalized PageRank. https://github.com/huynhnhathao/job_recommender
- **sentence-transformers / all-MiniLM-L6-v2** — эмбеддинги навыков.
- **pgvector (HNSW)** — ANN-retrieval в самом PostgreSQL.

**Non-functional опора:** GDPR Art.17 (right to erasure) · Doshi-Velez,
*"Towards a Rigorous Science of Interpretable ML"* (обоснование explainable-by-design).

---

## 15. Examples

### Example A — GOOD MATCH (score ≈ 0.89)

```
Developer:  skills[python,pytorch,ml] · exp 6y · async 0.8 team 0.6 ·
            motivation{learning 0.9, innovation 0.8} · UTC+1 · goals["move to ML"]
Project:    required[ml,python] · async-heavy · innovation 0.9 ·
            growth["ml","distributed systems"] · window UTC+0..UTC+3
Component:  skill 0.92 · workstyle 0.85 · motivation 0.88 · tz 1.00 · growth 0.80
MATCH = 0.30·0.92 + 0.25·0.85 + 0.20·0.88 + 0.15·1.00 + 0.10·0.80 = 0.89
Explanation: «Сильное совпадение по ML-стеку, усиленное опытом; async-стиль и
            тяга к обучению совпадают с инновационным async-проектом; цель
            "move to ML" прямо закрывается growth-возможностями.»
Risks: none significant.
```

### Example B — BAD MATCH / SKILL-ONLY TRAP (score ≈ 0.36)

```
Developer:  skills[python,ml] · exp 6y · async 0.2 team 0.2 ·
            motivation{stability 0.9, learning 0.3} · UTC+9 · goals["stay backend"]
Project:    required[ml,python] · async-heavy · innovation 0.9 ·
            growth["ml","distributed systems"] · window UTC-5..UTC-2
Component:  skill 0.90 · workstyle 0.15 · motivation 0.20 · tz 0.00 · growth 0.10
MATCH = 0.30·0.90 + 0.25·0.15 + 0.20·0.20 + 0.15·0.00 + 0.10·0.10 = 0.36
>>> КЛЮЧЕВОЙ КЕЙС: навыки ИДЕНТИЧНЫ примеру A (skill 0.90), но система отвергает
    матч. «Just ship by skills» отгрузил бы это — поведенческий слой поймал
    рассогласование стиля, мотивации и таймзоны. Доказательство, что система
    НЕ skill-only.
Risks: timezone-конфликт (18h) · sync-vs-async · stability-vs-innovation.
```

### Example C — LLM I/O CONTRACT (что слой объяснения получает и отдаёт)

```
INPUT (только скоры + структурные факты, БЕЗ сырых векторов):
  { match_score: 0.36, skill: 0.90, workstyle: 0.15, motivation: 0.20,
    timezone: 0.00, growth: 0.10,
    developer_timezone: "UTC+9", project_window: "UTC-5..UTC-2",
    career_goals: ["stay backend"], growth_opportunities: ["ml","distributed systems"] }
  // work_style_vector / motivation_vector НЕ передаются (privacy + audit)
OUTPUT (структурированный):
  { explanation: "<3 секции: Skill Alignment / Behavioral Fit / Growth Potential>",
    risks: ["timezone mismatch (18h)", "sync-vs-async clash", "stability-vs-innovation"],
    growth_potential: ["ml"] }
```

### Example D — REALLOCATION / BURNOUT INTERVENTION (двигатель ре-оптимизации)

```
Состояние: оператор OP-114 «перегревается» на текущем проекте.
Developer OP-114: skills[python,fastapi,ml] · exp 7y · async 0.7 team 0.5 ·
            motivation{stability 0.8, learning 0.5, innovation 0.4} ·
            UTC+1 · goals["move to ML","sustainable pace"]
Current → Project ATLAS: intensity 0.9 · innovation-heavy · 50 недель подряд
            при intensity ≥ 0.8 · motivation_alignment 0.3 (низкая)

ШАГ 1 — ДЕТЕКТ (детерминированно):
  burnout(ATLAS) = min(1.0, (50/48) · 0.9 · (1 − 0.3)) = min(1.0, 0.66) = 0.66
  >>> 0.66 > 0.6  ->  badge HIGH  ->  ТРИГГЕР реаллокации

ШАГ 2 — ПОИСК ЗАМЕНЫ (тот же движок, skill-bridge фильтр intensity ≤ 0.6):
  Candidate BRIDGE-7: required[python,ml] · intensity 0.5 · innovation 0.6 ·
            growth["ml","mlops"] · window UTC+0..UTC+3
  Match(OP-114 <-> BRIDGE-7):
    skill 0.88 · workstyle 0.80 · motivation 0.75 · tz 1.00 · growth 0.85
    MATCH = 0.30·0.88 + 0.25·0.80 + 0.20·0.75 + 0.15·1.00 + 0.10·0.85 = 0.85

ШАГ 3 — ПРОГНОЗ ПОСЛЕ ПЕРЕХОДА:
  intensity 0.5 + сброс high-intensity streak  ->  projected burnout ≈ 0.12

OUTPUT (предложение, не авто-перемещение — менеджер подтверждает):
  { developer_id: "OP-114", action: "reallocate",
    from:  { project: "ATLAS",    burnout: 0.66, badge: "HIGH" },
    to:    { project: "BRIDGE-7", match: 0.85, projected_burnout: 0.12 },
    type:  "skill-bridge",
    rationale: "Снижает интенсивность 0.9->0.5 и закрывает цель 'move to ML' через
                ML/MLOps growth-возможности — устойчивый темп без потери траектории.",
    risk_delta: { burnout: -0.54 } }

>>> Система не просто показывает риск — она ДЕЙСТВУЕТ: тем же детерминированным
    движком находит skill-bridge переход, который гасит burnout и продвигает
    career_goal. Закрывает failure-condition «static allocation».
```

**Покрытие примеров:** A (good) · B (skill-only trap) · C (LLM I/O) · D (reallocation)
— все четыре failure-conditions и оба слоя (матчинг + ре-оптимизация).

---

## Приложение — сжатый `--idea` для ForgeLoop

```
NEURAL SYNC — платформа нейро-аллокации «оператор<->проект». Детерминированное ядро
матчинга (MATCH_SCORE = взвешенная сумма по skill/workstyle/motivation/timezone/growth,
гибрид: пересечение навыков + семантическая близость эмбеддингов) строго отделено от
LLM-слоя объяснения, который получает только предвычисленные скоры (не сырые
поведенческие векторы) и генерирует «почему матч / риски / пути роста»; LLM-провайдер
взаимозаменяем через артефакт-промт. ANN-retrieval (pgvector HNSW) для масштаба 10k+.
Двигатель ре-оптимизации: bench/burnout-prediction + предложение реаллокации через тот
же движок. FastAPI + pgvector + React, 3 роли (dev/manager/admin), GDPR + explainable AI,
латентность <500ms. Провал = матчинг только по скиллам / нет объяснимости / статичная аллокация.
```
