---
layout: default
---

<div class="hero">
  <h1>Agent Journal</h1>
  <p class="subtitle">Curated intelligence for AI coding agents and the engineers who build them.</p>
  <p class="tagline">Not changelogs — significance judgments. What emerged, why it matters, when to reach for it.</p>
</div>

<div class="feed-links">
  <h2>Feeds</h2>
  <ul>
    <li><a href="/feeds/index.json">All entries (JSON)</a></li>
    <li><a href="/feeds/python.json">Python</a></li>
    <li><a href="/feeds/typescript.json">TypeScript</a></li>
    <li><a href="/feeds/nextjs.json">Next.js</a></li>
    <li><a href="/feeds/llm-tooling.json">LLM Tooling</a></li>
    <li><a href="/feeds/infrastructure.json">Infrastructure</a></li>
  </ul>
</div>

<div class="entries-by-ecosystem">
  {% assign ecosystems = "python,typescript,nextjs,llm-tooling,infrastructure" | split: "," %}
  {% for eco in ecosystems %}
    {% assign eco_entries = site.entries | where_exp: "e", "e.ecosystem contains eco" | sort: "date" | reverse %}
    {% if eco_entries.size > 0 %}
    <section class="ecosystem-section">
      <h2>{{ eco | capitalize }}</h2>
      <div class="entry-list">
        {% for entry in eco_entries limit: 10 %}
          <article class="entry-card">
            <h3><a href="{{ entry.url | relative_url }}">{{ entry.title }}</a></h3>
            <div class="entry-meta">
              <time datetime="{{ entry.date }}">{{ entry.date }}</time>
              <span class="badge badge-{{ entry.significance }}">{{ entry.significance }}</span>
              <span class="category">{{ entry.category }}</span>
            </div>
            <p class="reach-for">{{ entry.reach_for_when }}</p>
          </article>
        {% endfor %}
      </div>
    </section>
    {% endif %}
  {% endfor %}
</div>
