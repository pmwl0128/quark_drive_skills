/** LocalAdapter: the bridge API (pcui serve). Handles the #bootstrap=<token> fragment once
 * (token -> HttpOnly cookie, then scrubbed from the URL); dev mode injects the CLI secret
 * via the vite proxy instead. Zod-validates every response at the boundary (design §3). */
import {
  ActualMatchupResponseDtoSchema, ArtifactAppendEventDtoSchema, CapabilitiesSchema,
  ConsoleStatusDtoSchema, DamageBatchResultDtoSchema, DiagnoseFactsDtoSchema,
  DamageResultDtoSchema, MetaDetailDtoSchema,
  OppCheckGridDtoSchema, OppDetailsDtoSchema, OppKoGridDtoSchema, OppSetCatalogDtoSchema,
  PokemonCardDtoSchema,
  RankingDtoSchema, ResolveEntryDtoSchema, SessionDtoSchema, SessionWithLedgerDtoSchema, SessionOverviewDtoSchema,
  LearnersDtoSchema, MetaFacetsDtoSchema, MetaKoDtoSchema,
  MetaKoTrendDtoSchema, SpeedBatchResultDtoSchema, TrendDtoSchema, TuneResultDtoSchema,
  TopMetaDetailDtoSchema, TopMetaRankingDtoSchema, UsageTrendDtoSchema,
  WorkspaceInfoDtoSchema, WorkspaceLibraryDtoSchema, WorkspaceRecordsDtoSchema, WorkspacePickerResultDtoSchema,
  type ActualMatchupRequestDto, type Capabilities, type DamageRequestDto, type FormatId,
  type SpeedInputDto, type TopMetaScopeId,
} from "@pokemon-champions/protocol";
import { z } from "zod";
import { TeamCodeResultDtoSchema } from "@pokemon-champions/protocol";
import {
  HttpError, fetchJson,
  type DexIndexEntry, type RuntimeAdapter, type RuntimeConfig, type SessionApi, type WorkspaceApi,
} from "./adapter.ts";
import { loadProjectionDexIndex } from "./online.ts";
import { SessionFeed } from "./sessionFeed.ts";

export class LocalAdapter implements RuntimeAdapter {
  async teamCode(code: string, format: FormatId) {
    return TeamCodeResultDtoSchema.parse(await this.post("/team/code", { code, format }));
  }
  readonly runtime = "local" as const;

  constructor(private cfg: RuntimeConfig) {}

  private workspaceId = "";
  readonly storage: WorkspaceApi = {
    info: async () => WorkspaceInfoDtoSchema.parse(await this.get("/workspace")),
    pickFolder: async lang => WorkspacePickerResultDtoSchema.parse(await this.post("/workspace/pick-folder", { workspaceId: this.workspaceId, lang })).path,
    library: async () => {
      const doc = WorkspaceLibraryDtoSchema.parse(await this.get("/workspace/library"));
      this.checkWorkspace(doc.workspaceId);
      return doc;
    },
    saveLibrary: async doc => WorkspaceLibraryDtoSchema.parse(await this.post("/workspace/library", doc)),
    importBrowser: async doc => { await this.post("/workspace/import-browser", doc); },
    records: async () => {
      const doc = WorkspaceRecordsDtoSchema.parse(await this.get("/workspace/records"));
      this.checkWorkspace(doc.workspaceId);
      return doc.records;
    },
    putRecord: async record => { await this.post("/workspace/records", { workspaceId: this.workspaceId, record }); },
    deleteRecords: async ids => { await this.post("/workspace/records/delete", { workspaceId: this.workspaceId, ids }); },
    switch: async (path, migrate) => WorkspaceInfoDtoSchema.parse(await this.post("/workspace/switch", {
      path, migrate, workspaceId: this.workspaceId,
    })),
  };

  private checkWorkspace(id: string) {
    if (id !== this.workspaceId) throw new HttpError(409, "Workspace changed; reload", { error: { code: "workspace_changed", message: "Workspace changed" } });
  }

  async init(): Promise<void> {
    const match = /[#&]bootstrap=([^&]+)/.exec(location.hash);
    if (match) {
      const resp = await fetch(`${this.cfg.apiBase}/bootstrap`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: match[1] }),
      });
      // Scrub the one-time token regardless — it is spent now, and leaving it in
      // history/bookmarks is worse. If the exchange failed (already-used/expired token, or a
      // StrictMode double-mount racing two POSTs of the same token), the capabilities() call
      // that follows will fail auth and RuntimeProvider surfaces an actionable message.
      history.replaceState(null, "", location.pathname + location.search);
      if (!resp.ok) console.warn(`pcui bootstrap exchange failed: ${resp.status}`);
    }
    this.workspaceId = (await this.storage.info()).workspaceId;
  }

  private get<T>(path: string): Promise<T> {
    return fetchJson<T>(`${this.cfg.apiBase}${path}`, {
      headers: this.workspaceId && path !== "/workspace" ? { "X-PCUI-Workspace": this.workspaceId } : {},
    });
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return fetchJson<T>(`${this.cfg.apiBase}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(this.workspaceId ? { "X-PCUI-Workspace": this.workspaceId } : {}) },
      body: JSON.stringify(body),
    });
  }

  async capabilities(): Promise<Capabilities> {
    return CapabilitiesSchema.parse(await this.get("/capabilities"));
  }

  async ranking(format: FormatId, limit = 300) {
    return RankingDtoSchema.parse(
      await this.get(`/meta/ranking?format=${format}&limit=${limit}`));
  }

  async detail(format: FormatId, slug: string) {
    return MetaDetailDtoSchema.parse(
      await this.get(`/meta/detail?format=${format}&pokemon=${encodeURIComponent(slug)}`));
  }

  async trend(format: FormatId) {
    return TrendDtoSchema.parse(await this.get(`/meta/trend?format=${format}`));
  }

  async usageTrend(format: FormatId, slug: string) {
    return UsageTrendDtoSchema.parse(
      await this.get(`/meta/usage-trend?format=${format}&pokemon=${encodeURIComponent(slug)}`));
  }

  async ko(format: FormatId, slug: string) {
    return MetaKoDtoSchema.parse(
      await this.get(`/meta/ko?format=${format}&pokemon=${encodeURIComponent(slug)}`));
  }

  async koTrend(format: FormatId, slug: string) {
    return MetaKoTrendDtoSchema.parse(
      await this.get(`/meta/ko-trend?format=${format}&pokemon=${encodeURIComponent(slug)}`));
  }

  async metaFacets(format: FormatId) {
    return MetaFacetsDtoSchema.parse(await this.get(`/meta/facets?format=${format}`));
  }

  async topRanking(format: FormatId, scope: TopMetaScopeId) {
    return TopMetaRankingDtoSchema.parse(
      await this.get(`/meta/top-ranking?format=${format}&scope=${scope}`));
  }

  async topDetail(format: FormatId, slug: string) {
    return TopMetaDetailDtoSchema.parse(
      await this.get(`/meta/top-detail?format=${format}&pokemon=${encodeURIComponent(slug)}`));
  }

  /** The browse index is projection data on BOTH runtimes (same static layer). */
  dexIndex(): Promise<DexIndexEntry[]> {
    return loadProjectionDexIndex(this.cfg.projectionBase);
  }

  async learners() {
    return LearnersDtoSchema.parse(await this.get("/dex/learners"));
  }

  async pokemonCard(nameOrSlug: string) {
    return PokemonCardDtoSchema.parse(
      await this.post("/dex/pokemon", { name: nameOrSlug }));
  }

  async resolve(names: string[], kind?: string) {
    return z.array(ResolveEntryDtoSchema).parse(
      await this.post("/dex/resolve/batch", { names, kind }));
  }

  async damage(req: DamageRequestDto) {
    return DamageResultDtoSchema.parse(await this.post("/calc/damage", req));
  }

  async damageBatch(items: DamageRequestDto[]) {
    return DamageBatchResultDtoSchema.parse(await this.post("/calc/batch", { items }));
  }

  async speedBatch(items: SpeedInputDto[]) {
    return SpeedBatchResultDtoSchema.parse(await this.post("/calc/speedbatch", { items }));
  }

  async oppDetails(format: FormatId, slug: string) {
    return OppDetailsDtoSchema.parse(await this.get(
      `/team/oppcache?format=${format}&view=details&species=${encodeURIComponent(slug)}`));
  }

  async oppSets(format: FormatId) {
    return OppSetCatalogDtoSchema.parse(await this.get(`/team/oppcache?format=${format}&view=sets`));
  }

  async oppKo(format: FormatId) {
    return OppKoGridDtoSchema.parse(await this.get(`/team/oppcache?format=${format}&view=ko`));
  }

  async oppChecks(format: FormatId) {
    return OppCheckGridDtoSchema.parse(await this.get(`/team/oppcache?format=${format}&view=checks`));
  }

  async actualMatchup(req: ActualMatchupRequestDto) {
    return ActualMatchupResponseDtoSchema.parse(await this.post("/team/matchup", req));
  }

  private feedInstance: SessionFeed | null = null;
  private get feed(): SessionFeed {
    return this.feedInstance ??= new SessionFeed(`${this.cfg.apiBase}/events`, async signal =>
      (await fetch(`${this.cfg.apiBase}/health`, { cache: "no-store", signal })).status);
  }

  /** UEP session surface (team.uep). Artifact payloads travel as verbatim text — the store's
   * round-trip guarantee ends the moment anyone re-serializes, so no JSON.parse here. */
  readonly sessions: SessionApi = {
    overview: async teamId => SessionOverviewDtoSchema.parse(await this.get(
      `/sessions/overview${teamId ? `?team_id=${encodeURIComponent(teamId)}` : ""}`)),
    list: async (options) => {
      const query = new URLSearchParams();
      if (options?.limit) query.set("limit", String(options.limit));
      if (options?.prioritizeDecisions) query.set("prioritize_decisions", "true");
      if (options?.archived) query.set("archived", options.archived);
      return z.array(SessionDtoSchema).parse(await this.get(`/sessions?${query}`));
    },
    create: async (meta) =>
      SessionDtoSchema.parse(await this.post("/sessions", { meta: meta ?? {} })),
    diagnosis: async (id) =>
      DiagnoseFactsDtoSchema.parse(await this.get(`/sessions/${encodeURIComponent(id)}/diagnose`)),
    rename: async (id, title) =>
      SessionDtoSchema.parse(await this.post(`/sessions/${encodeURIComponent(id)}/title`, { title })),
    archive: async (id, archived) =>
      SessionDtoSchema.parse(await this.post(`/sessions/${encodeURIComponent(id)}/archive`, { archived })),
    remove: async (id) => {
      await fetchJson(`${this.cfg.apiBase}/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE", headers: this.workspaceId ? { "X-PCUI-Workspace": this.workspaceId } : {},
      });
    },
    get: async (id) =>
      SessionWithLedgerDtoSchema.parse(await this.get(`/sessions/${encodeURIComponent(id)}`)),
    artifact: async (hash) => {
      const resp = await fetch(`${this.cfg.apiBase}/artifacts/${encodeURIComponent(hash)}`);
      if (!resp.ok) throw new HttpError(resp.status, `${resp.status} artifact ${hash}`);
      return { kind: resp.headers.get("X-Artifact-Kind") ?? "", text: await resp.text() };
    },
    putArtifact: async (sessionId, kind, payload, revision) =>
      ArtifactAppendEventDtoSchema.parse(await this.post(
        `/sessions/${encodeURIComponent(sessionId)}/artifacts`, { kind, payload, revision })),
    subscribe: (onChange) => this.feed.subscribe(onChange),
    liveStatus: () => this.feed.liveStatus(),
    onLiveStatus: (listener) => this.feed.onLiveStatus(listener),
    consoleStatus: async () => ConsoleStatusDtoSchema.parse(await this.get("/console")),
  };

  async tune(team: unknown, benchmarks: unknown[]) {
    return TuneResultDtoSchema.parse(
      await this.post("/team/tune", { team, benchmarks }));
  }
}
