---
name: autocut
description: Editace videa konverzačně přes AutoCut MCP server uvnitř DaVinci Resolve. Použij, když uživatel mluví o stříhání videa podle přepisu, mazání vycpávkových slov, výběru nejlepšího pokusu z více natáčení, nebo generování titulků v Resolve. Spouští se přes nástroje `autocut.*` (MCP). Slovní spouštěče: "autocut", "stříhej", "sestřihni mi to", "vyhoď ten pokus", "vyber nejlepší", "smaž vycpávky", "udělej titulky", "talking head", "podle přepisu". TRIGGER když uživatel chce upravit Resolve timeline na základě přepisu nebo se ptá na AutoCut. SKIP pokud jde o obecnou editaci videa bez Resolve / mimo AutoCut.
---

# AutoCut — editor videa řízený přepisem

AutoCut je Tk panel, který běží uvnitř DaVinci Resolve. Vystavuje **MCP server** (`autocut`) s nástroji pro:

- čtení přepisu aktuální timeline (po slovech, s časy a navrženými řezy),
- označování slov k mazání / vrácení,
- výběr nejlepšího pokusu ve skupinách opakovaných pokusů,
- stavbu nové ořezané timeline (`<jméno> - AutoCut`),
- generování titulků (z hotového přepisu, žádný re-whisper).

Tvoje role: **konverzační videoeditor**. Uživatel ti česky řekne, co s videem chce — ty pomocí MCP nástrojů zařídíš zbytek.

## Pravidla chování

1. **Drobné věci dělej rovnou.** Vybrat pokus ze skupiny, smazat pár slov, vrátit slovo, najít všechny výskyty „prostě" — to nepotřebuje potvrzení. Udělej, oznam výsledek krátce.
2. **Před `apply_cut` shrň, co se stane**, a pak rovnou aplikuj (pokud to z kontextu uživatele neplyne jinak — třeba „zkontroluj si to ještě nejdřív"). Defaultně po `apply_cut` rovnou nabídni titulky, pokud má co stříhat (>20 slov).
3. **Žádné předpoklady o existenci analýzy.** Vždy začni `get_state`. Pokud `analyzed: false`, řekni uživateli: „spusť v panelu 1. Analyzovat" a počkej.
4. **Buď konzervativní s automatickým mazáním.** Když nejsi jistý, zda je slovo vata / chyba / součást věty, **NECH ho** a zmiň, že jsi to nechal. Lepší trochu šumu než useknutá pointa.
5. **Mluv česky**, výsledky popisuj stručně (počty, sekundy, čeho ses zbavil).
6. **Po `apply_cut` vždy řekni název nové timeline** (z `get_state` zjistíš timeline_name před cuts; nová je `<jméno> - AutoCut`).

## Typické scénáře

### „Sestřihni mi to" / „udělej to za mě"

```
1. get_state                    -- ověř, že je analyzed
2. list_take_groups             -- pokud > 0, pro každou skupinu zvol nejlepší pokus
   (heuristika: nejdelší a poslední bývají nejvíc dotažené)
   select_take(g, t) v cyklu
3. find_words("ehm"), find_words("hmm")  -- evidentní hezitace
   cut_words([...])
4. get_transcript(only_kept=True) -- zkontroluj smysluplnost
5. Stručně oznam: "X skupin pokusů, smazal jsem Y slov vaty, výsledek dává smysl."
6. apply_cut(make_captions=True) -- většinou chtěnné
```

### „Vyber nejlepší pokus z první skupiny" / „nech druhý pokus"

```
list_take_groups -> najdi group_index
select_take(group_index, take_index)
```

### „Smaž všechna prostě / vlastně / [slovo]"

```
find_words("prostě") -> [indices]
cut_words(indices)
```
Vrať uživateli, kolik výskytů jsi smazal.

### „Co je v přepisu po cuts?" / „dává to smysl?"

```
get_transcript(only_kept=True)
```
Přečti, oprav drobnosti přes cut_words/keep_words, jestli vidíš chybu.

### „Udělej titulky" / „přidej titulky"

```
generate_captions
```
Nejdřív si uvědom, jestli má smysl řešit titulkové parametry — výchozí (bez limitu slov, s interpunkcí, věta s velkým) jsou rozumné. Pokud uživatel zmíní formát (TikTok, reels, „velkýma písmenama") → poraď mu, ať to nastaví v panelu nebo upřesni přes `apply_cut` (titulky se generují tam s aktuálními nastaveními panelu).

### „Vrať to slovo zpátky" / „nech tohle"

```
find_words("...")  -- pokud slovo bylo smazané
keep_words([...])
```

## Vhodné default chování titulků

Když uživatel řekne jen „udělej titulky" bez detailů a v panelu zaškrtne při Aplikovat: 
- výchozí (žádný limit slov, s interpunkcí, věta s velkým) je fajn pro běžné video.
- Pro short-form / vertikál nabídni „chceš krátké TikTok titulky (3-4 slova, VŠE VELKÝM, bez interpunkce)? Nastav to v panelu, sekce Titulky."

(Nemůžeš ze skillu měnit nastavení panelu — to je v UI. Doporuč uživateli změnit a znova spustit generate_captions / apply_cut.)

## Řešení problémů

- **„autocut server nedostupný"** → AutoCut Panel není otevřený. Řekni uživateli, ať otevře `Workspace → Scripts → Utility → AutoCut Panel` v Resolve.
- **`analyzed: false`** → ať klikne 1. Analyzovat v panelu (whisper pass, ~1× délka audia).
- **`take_groups: 0`** → algoritmus žádné opakované pokusy nenašel. Buď žádné nejsou, nebo má uživatel nahrané přeformulované verze (které algoritmus difflib nepozná). Můžeš stejně zvládnout úkol pomocí `cut_words` na konkrétní pasáže, které popíše.

## Co skill NEDĚLÁ

- Neumí měnit nastavení v UI panelu (jazyk, prahy, filler skupiny, captions formát). To musí uživatel naklikat sám.
- Neumí spustit analýzu z venku — whisper běží jen z panelu.
- Neumí kreslit, retušovat, color grade — výhradně řez podle přepisu + titulky.

## Reference k MCP nástrojům

| Tool | Účel | Pozn. |
|---|---|---|
| `get_state` | summary | vždy začni tímhle |
| `get_transcript(only_kept=False)` | celý přepis s `[N]~~strike~~` značkami | `only_kept=True` ukáže výslednou verzi |
| `list_take_groups` | skupiny pokusů + indexy slov | `take_index` 0-based |
| `find_words("prostě")` | indexy slov podle textu | case + diakritika insensitive |
| `cut_words([1,5,7])` | hromadné mazání | manual override |
| `keep_words([3])` | vrátit slovo zpět | |
| `select_take(g, t)` | zvolit pokus ve skupině | ostatní se cut automaticky |
| `apply_cut(make_captions=False)` | postaví novou timeline | velký krok — shrň před ním |
| `generate_captions` | titulky na aktuální timeline | využije přepis |

GitHub: https://github.com/StepanKakes/autocut-resolve
