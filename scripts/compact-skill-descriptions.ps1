param(
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }),
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$descriptions = @{
  "codex-empire" = "Use the Codex Empire wiki skill pack for vault setup, ingest, query, lint, save, research, canvas, and Obsidian Markdown workflows."
  "wiki" = "Set up and maintain a Codex + Obsidian wiki vault. Routes setup, ingest, query, lint, save, research, canvas, and hot cache work."
  "wiki-ingest" = "Ingest files, URLs, and batches into the Obsidian wiki. Extract entities, concepts, cross-links, sources, and update logs."
  "wiki-query" = "Answer questions from the Obsidian wiki using hot cache, index, relevant pages, citations, and quick, standard, or deep modes."
  "wiki-lint" = "Audit an Obsidian wiki for orphan pages, dead links, stale claims, frontmatter gaps, empty sections, and cross-link issues."
  "wiki-fold" = "Roll up wiki log entries into linked fold pages using extractive summaries. Triggers on wiki-fold, fold the log, log rollup."
  "save" = "Save a conversation, answer, decision, or insight as a structured Obsidian wiki note. Triggers on /save, save this, file this."
  "autoresearch" = "Research a topic iteratively, synthesize sources, and file findings into the Obsidian wiki. Triggers on /autoresearch, research, investigate."
  "canvas" = "Create and update Obsidian canvas files with notes, images, PDFs, text cards, zones, and wiki visual maps. Triggers on /canvas."
  "defuddle" = "Clean web pages into readable Markdown before wiki ingest. Removes ads, navigation, boilerplate, and clutter. Triggers on defuddle or clean URL."
  "obsidian-markdown" = "Write Obsidian Markdown with wikilinks, embeds, callouts, properties, tags, highlights, math, and canvas syntax."
  "obsidian-bases" = "Create and edit Obsidian Bases database views, filters, formulas, tables, cards, lists, and vault dashboards."

  "cybersecurity" = "Run security code reviews for vulnerabilities, authorization, secrets, dependencies, IaC, threat modeling, AI code risk, and compliance."
  "seo" = "Run comprehensive SEO audits and optimization across technical SEO, content, schema, sitemaps, images, performance, GEO, and AI search."
  "github" = "Optimize GitHub repositories with README, metadata, legal, community, release, SEO, and portfolio workflows."
  "github-audit" = "Audit GitHub repository health across README, metadata, legal, community, releases, SEO, and discoverability."
  "github-community" = "Set up GitHub community health files, templates, CODEOWNERS, contributing docs, support docs, and CI/dependabot basics."
  "github-empire" = "Improve GitHub portfolio presence, profile README, repo metadata, branding, cross-links, topics, and public positioning."
  "github-legal" = "Handle GitHub repository licensing, attribution, notices, security policy, citation, and legal compliance basics."
  "github-meta" = "Optimize GitHub repository metadata, description, topics, homepage, settings, social preview guidance, and language bar files."
  "github-readme" = "Create or improve GitHub READMEs with structure, badges, installation, usage, SEO, attribution, and banner guidance."
  "github-release" = "Plan GitHub releases, changelogs, versioning, tags, release notes, badges, and package distribution guidance."
  "github-seo" = "Research and optimize GitHub repository keywords, topics, descriptions, README search visibility, and AI citability."
  "wan2gp-operator" = "Operate Wan2GP setup, readiness checks, settings generation, headless jobs, failure diagnosis, and upstream release checks."
  "banana" = "Generate or edit images, banners, logos, visuals, and creative assets using the configured image generation workflow."
}

function Get-DescriptionLength {
  param([string]$Text)

  if ($Text -notmatch "(?s)^---\s*\r?\n(.*?)\r?\n---") {
    return 0
  }

  $frontmatter = $Matches[1]
  if ($frontmatter -match "(?ms)^description:\s*>\s*\r?\n(?<block>(?:\s+.*\r?\n?)+)") {
    return (($Matches["block"] -replace "(?m)^\s+", " ").Trim()).Length
  }

  if ($frontmatter -match "(?m)^description:\s*(?<desc>.*)$") {
    return $Matches["desc"].Trim().Length
  }

  return 0
}

function Set-SkillDescription {
  param(
    [string]$Path,
    [string]$Description
  )

  $text = Get-Content -Raw -LiteralPath $Path
  $oldLength = Get-DescriptionLength -Text $text

  if ($text -notmatch "(?s)^---\s*\r?\n(.*?)\r?\n---") {
    throw "No YAML frontmatter found in $Path"
  }

  $frontmatter = $Matches[1]
  $newLine = "description: $Description"

  if ($frontmatter -match "(?ms)^description:\s*>\s*\r?\n(?:\s+.*\r?\n?)+") {
    $updated = [regex]::Replace($text, "(?ms)^description:\s*>\s*\r?\n(?:\s+.*\r?\n?)+", $newLine, 1)
  } elseif ($frontmatter -match "(?m)^description:\s*.*$") {
    $updated = [regex]::Replace($text, "(?m)^description:\s*.*$", $newLine, 1)
  } else {
    $updated = [regex]::Replace($text, "(?m)^name:\s*.*$", "`$0`r`n$newLine", 1)
  }

  $newLength = $Description.Length
  [pscustomobject]@{
    Path = $Path
    OldChars = $oldLength
    NewChars = $newLength
    Saved = [Math]::Max(0, $oldLength - $newLength)
  }

  if (-not $DryRun) {
    Set-Content -LiteralPath $Path -Value $updated -NoNewline
  }
}

$roots = @(
  (Join-Path $CodexHome "skills"),
  (Join-Path $CodexHome "agents\skills")
)

$results = @()
foreach ($root in $roots) {
  if (-not (Test-Path -LiteralPath $root)) {
    continue
  }

  foreach ($name in $descriptions.Keys) {
    $path = Join-Path $root "$name\SKILL.md"
    if (Test-Path -LiteralPath $path) {
      $results += Set-SkillDescription -Path $path -Description $descriptions[$name]
    }
  }
}

if ($results.Count -eq 0) {
  Write-Output "No matching installed skills found."
  exit 0
}

$results | Sort-Object Saved -Descending | Format-Table -AutoSize
$totalSaved = ($results | Measure-Object -Property Saved -Sum).Sum
Write-Output "Updated $($results.Count) skill descriptions. Saved $totalSaved description characters."
if ($DryRun) {
  Write-Output "Dry run: no files changed."
} else {
  Write-Output "Restart Codex to reload compact skill descriptions."
}
