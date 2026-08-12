export namespace main {

	export class UIConfig {
	    scale: number;
	    density: string;
	    sidebar_width: number;
	    artwork_size: number;
	    motion: string;
	    player_volume: number;
	    startup_destination: string;
	    remember_last_page: boolean;
	    open_downloads_on_add: boolean;
	    completion_notifications: boolean;
	    device_notifications: boolean;
	    completed_history_retention: number;

	    static createFrom(source: any = {}) {
	        return new UIConfig(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.scale = source["scale"];
	        this.density = source["density"];
	        this.sidebar_width = source["sidebar_width"];
	        this.artwork_size = source["artwork_size"];
	        this.motion = source["motion"];
	        this.player_volume = source["player_volume"];
	        this.startup_destination = source["startup_destination"];
	        this.remember_last_page = source["remember_last_page"];
	        this.open_downloads_on_add = source["open_downloads_on_add"];
	        this.completion_notifications = source["completion_notifications"];
	        this.device_notifications = source["device_notifications"];
	        this.completed_history_retention = source["completed_history_retention"];
	    }
	}
	export class Config {
	    config_schema_version: number;
	    ui?: UIConfig;
	    download_path: string;
	    download_path_is_library_root?: boolean;
	    apple_enabled: boolean;
	    apple_authorization_token?: string;
	    apple_music_user_token?: string;
	    apple_storefront?: string;
	    apple_wvd_path?: string;
	    amazon_enabled: boolean;
	    amazon_direct_creds_json?: string;
	    amazon_wvd_path?: string;
	    amazon_region?: string;
	    qobuz_enabled: boolean;
	    qobuz_email?: string;
	    qobuz_password?: string;
	    qobuz_app_id?: string;
	    qobuz_app_secret?: string;
	    qobuz_user_auth_token?: string;
	    deezer_arl_token?: string;
	    deezer_bf_secret?: string;
	    sources_enabled?: string[];
	    first_run_complete: boolean;
	    output_format?: string;
	    max_retries?: number;
	    max_concurrent_jobs?: number;
	    library_mode?: string;
	    prefer_explicit?: boolean;
	    strict_matching: boolean;
	    folder_structure?: string;
	    album_folder_structure?: string;
	    playlist_folder_structure?: string;
	    single_track_structure?: string;
	    filename_format?: string;
	    single_track_filename_template?: string;
	    album_zip_name_template?: string;
	    album_track_filename_template?: string;
	    folder_structure_template?: string;
	    multi_disc_handling?: string;
	    track_number_padding?: number;
	    illegal_character_replacement?: string;
	    whitespace_handling?: string;
	    filename_conflict_behavior?: string;
	    fetch_lyrics: boolean;
	    spotify_sp_dc?: string;
	    tidal_enabled: boolean;
	    tidal_auth_mode?: string;
	    tidal_session_json?: string;
	    tidal_access_token?: string;
	    tidal_refresh_token?: string;
	    tidal_session_id?: string;
	    tidal_token_type?: string;
	    tidal_country_code?: string;
	    antra_api_key?: string;
	    theme?: string;
	    download_source?: string;
	    download_sources?: string[];
	    save_cover_art_sidecar: boolean;
	    auto_sync_enabled: boolean;
	    auto_sync_hour: number;
	    auto_sync_minute: number;
	    auto_sync_days: number;
	    tracked_playlists?: any[];

	    static createFrom(source: any = {}) {
	        return new Config(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.config_schema_version = source["config_schema_version"];
	        this.ui = this.convertValues(source["ui"], UIConfig);
	        this.download_path = source["download_path"];
	        this.download_path_is_library_root = source["download_path_is_library_root"];
	        this.apple_enabled = source["apple_enabled"];
	        this.apple_authorization_token = source["apple_authorization_token"];
	        this.apple_music_user_token = source["apple_music_user_token"];
	        this.apple_storefront = source["apple_storefront"];
	        this.apple_wvd_path = source["apple_wvd_path"];
	        this.amazon_enabled = source["amazon_enabled"];
	        this.amazon_direct_creds_json = source["amazon_direct_creds_json"];
	        this.amazon_wvd_path = source["amazon_wvd_path"];
	        this.amazon_region = source["amazon_region"];
	        this.qobuz_enabled = source["qobuz_enabled"];
	        this.qobuz_email = source["qobuz_email"];
	        this.qobuz_password = source["qobuz_password"];
	        this.qobuz_app_id = source["qobuz_app_id"];
	        this.qobuz_app_secret = source["qobuz_app_secret"];
	        this.qobuz_user_auth_token = source["qobuz_user_auth_token"];
	        this.deezer_arl_token = source["deezer_arl_token"];
	        this.deezer_bf_secret = source["deezer_bf_secret"];
	        this.sources_enabled = source["sources_enabled"];
	        this.first_run_complete = source["first_run_complete"];
	        this.output_format = source["output_format"];
	        this.max_retries = source["max_retries"];
	        this.max_concurrent_jobs = source["max_concurrent_jobs"];
	        this.library_mode = source["library_mode"];
	        this.prefer_explicit = source["prefer_explicit"];
	        this.strict_matching = source["strict_matching"];
	        this.folder_structure = source["folder_structure"];
	        this.album_folder_structure = source["album_folder_structure"];
	        this.playlist_folder_structure = source["playlist_folder_structure"];
	        this.single_track_structure = source["single_track_structure"];
	        this.filename_format = source["filename_format"];
	        this.single_track_filename_template = source["single_track_filename_template"];
	        this.album_zip_name_template = source["album_zip_name_template"];
	        this.album_track_filename_template = source["album_track_filename_template"];
	        this.folder_structure_template = source["folder_structure_template"];
	        this.multi_disc_handling = source["multi_disc_handling"];
	        this.track_number_padding = source["track_number_padding"];
	        this.illegal_character_replacement = source["illegal_character_replacement"];
	        this.whitespace_handling = source["whitespace_handling"];
	        this.filename_conflict_behavior = source["filename_conflict_behavior"];
	        this.fetch_lyrics = source["fetch_lyrics"];
	        this.spotify_sp_dc = source["spotify_sp_dc"];
	        this.tidal_enabled = source["tidal_enabled"];
	        this.tidal_auth_mode = source["tidal_auth_mode"];
	        this.tidal_session_json = source["tidal_session_json"];
	        this.tidal_access_token = source["tidal_access_token"];
	        this.tidal_refresh_token = source["tidal_refresh_token"];
	        this.tidal_session_id = source["tidal_session_id"];
	        this.tidal_token_type = source["tidal_token_type"];
	        this.tidal_country_code = source["tidal_country_code"];
	        this.antra_api_key = source["antra_api_key"];
	        this.theme = source["theme"];
	        this.download_source = source["download_source"];
	        this.download_sources = source["download_sources"];
	        this.save_cover_art_sidecar = source["save_cover_art_sidecar"];
	        this.auto_sync_enabled = source["auto_sync_enabled"];
	        this.auto_sync_hour = source["auto_sync_hour"];
	        this.auto_sync_minute = source["auto_sync_minute"];
	        this.auto_sync_days = source["auto_sync_days"];
	        this.tracked_playlists = source["tracked_playlists"];
	    }

		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class HistoryItem {
	    date: string;
	    url: string;
	    title?: string;
	    artwork_url?: string;
	    total: number;
	    downloaded: number;
	    failed: number;
	    skipped: number;
	    error?: string;
	    sources: Record<string, number>;
	    completed_files?: string[];

	    static createFrom(source: any = {}) {
	        return new HistoryItem(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.date = source["date"];
	        this.url = source["url"];
	        this.title = source["title"];
	        this.artwork_url = source["artwork_url"];
	        this.total = source["total"];
	        this.downloaded = source["downloaded"];
	        this.failed = source["failed"];
	        this.skipped = source["skipped"];
	        this.error = source["error"];
	        this.sources = source["sources"];
	        this.completed_files = source["completed_files"];
	    }
	}
	export class IPodBackupDeleteRequest {
	    archive_id: string;
	    snapshot_id: string;
	    confirmed: boolean;

	    static createFrom(source: any = {}) {
	        return new IPodBackupDeleteRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	        this.confirmed = source["confirmed"];
	    }
	}
	export class IPodBackupExportRequest {
	    archive_id: string;
	    snapshot_id: string;
	    destination_dir: string;

	    static createFrom(source: any = {}) {
	        return new IPodBackupExportRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	        this.destination_dir = source["destination_dir"];
	    }
	}
	export class IPodBackupNoteRequest {
	    archive_id: string;
	    snapshot_id: string;
	    note: string;

	    static createFrom(source: any = {}) {
	        return new IPodBackupNoteRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	        this.note = source["note"];
	    }
	}
	export class IPodBackupSnapshotRequest {
	    archive_id: string;
	    snapshot_id: string;

	    static createFrom(source: any = {}) {
	        return new IPodBackupSnapshotRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	    }
	}
	export class IPodBackupSnapshotsRequest {
	    archive_id: string;
	    page: number;
	    page_size: number;

	    static createFrom(source: any = {}) {
	        return new IPodBackupSnapshotsRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.page = source["page"];
	        this.page_size = source["page_size"];
	    }
	}
	export class IPodBackupVerifyRequest {
	    archive_id: string;
	    snapshot_id: string;

	    static createFrom(source: any = {}) {
	        return new IPodBackupVerifyRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	    }
	}
	export class IPodBrowseRequest {
	    mount_path: string;
	    resource: string;
	    page: number;
	    page_size: number;

	    static createFrom(source: any = {}) {
	        return new IPodBrowseRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mount_path = source["mount_path"];
	        this.resource = source["resource"];
	        this.page = source["page"];
	        this.page_size = source["page_size"];
	    }
	}
	export class IPodCapacityUnlockAdvanceRequest {
	    session_id: string;
	    action: string;
	    confirmed: boolean;
	    data?: Record<string, any>;

	    static createFrom(source: any = {}) {
	        return new IPodCapacityUnlockAdvanceRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.session_id = source["session_id"];
	        this.action = source["action"];
	        this.confirmed = source["confirmed"];
	        this.data = source["data"];
	    }
	}
	export class IPodCapacityUnlockEligibilityRequest {
	    mount_path: string;

	    static createFrom(source: any = {}) {
	        return new IPodCapacityUnlockEligibilityRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mount_path = source["mount_path"];
	    }
	}
	export class IPodCapacityUnlockStartRequest {
	    mount_path: string;
	    confirmed: boolean;
	    acknowledgements?: Record<string, boolean>;

	    static createFrom(source: any = {}) {
	        return new IPodCapacityUnlockStartRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mount_path = source["mount_path"];
	        this.confirmed = source["confirmed"];
	        this.acknowledgements = source["acknowledgements"];
	    }
	}
	export class IPodExecuteRequest {
	    plan_id: string;
	    confirmed: boolean;

	    static createFrom(source: any = {}) {
	        return new IPodExecuteRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.plan_id = source["plan_id"];
	        this.confirmed = source["confirmed"];
	    }
	}
	export class IPodManualBackupRequest {
	    mount_path: string;

	    static createFrom(source: any = {}) {
	        return new IPodManualBackupRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mount_path = source["mount_path"];
	    }
	}
	export class IPodMigrationPreflightRequest {
	    archive_id: string;
	    snapshot_id: string;
	    mount_path: string;

	    static createFrom(source: any = {}) {
	        return new IPodMigrationPreflightRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	        this.mount_path = source["mount_path"];
	    }
	}
	export class IPodMigrationRequest {
	    migration_plan_id: string;
	    confirmed: boolean;

	    static createFrom(source: any = {}) {
	        return new IPodMigrationRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.migration_plan_id = source["migration_plan_id"];
	        this.confirmed = source["confirmed"];
	    }
	}
	export class IPodPlanDetailsRequest {
	    plan_id: string;
	    group: string;
	    page: number;
	    page_size: number;

	    static createFrom(source: any = {}) {
	        return new IPodPlanDetailsRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.plan_id = source["plan_id"];
	        this.group = source["group"];
	        this.page = source["page"];
	        this.page_size = source["page_size"];
	    }
	}
	export class IPodPlanRequest {
	    mount_path: string;
	    source_files: string[];
	    staging_id?: string;

	    static createFrom(source: any = {}) {
	        return new IPodPlanRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mount_path = source["mount_path"];
	        this.source_files = source["source_files"];
	        this.staging_id = source["staging_id"];
	    }
	}
	export class IPodRecoveryUSBDevice {
	    vendor_id: string;
	    product_id: string;
	    mode: string;
	    model_hint?: string;
	    name?: string;
	    instance_id?: string;

	    static createFrom(source: any = {}) {
	        return new IPodRecoveryUSBDevice(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.vendor_id = source["vendor_id"];
	        this.product_id = source["product_id"];
	        this.mode = source["mode"];
	        this.model_hint = source["model_hint"];
	        this.name = source["name"];
	        this.instance_id = source["instance_id"];
	    }
	}
	export class IPodRecoveryUSBInspection {
	    supported: boolean;
	    available: boolean;
	    read_only: boolean;
	    platform: string;
	    devices: IPodRecoveryUSBDevice[];
	    message?: string;
	    error?: string;

	    static createFrom(source: any = {}) {
	        return new IPodRecoveryUSBInspection(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.supported = source["supported"];
	        this.available = source["available"];
	        this.read_only = source["read_only"];
	        this.platform = source["platform"];
	        this.devices = this.convertValues(source["devices"], IPodRecoveryUSBDevice);
	        this.message = source["message"];
	        this.error = source["error"];
	    }

		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class IPodRestorePreflightRequest {
	    archive_id: string;
	    snapshot_id: string;
	    mount_path: string;

	    static createFrom(source: any = {}) {
	        return new IPodRestorePreflightRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.archive_id = source["archive_id"];
	        this.snapshot_id = source["snapshot_id"];
	        this.mount_path = source["mount_path"];
	    }
	}
	export class IPodRestoreRequest {
	    restore_plan_id: string;
	    confirmed: boolean;

	    static createFrom(source: any = {}) {
	        return new IPodRestoreRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.restore_plan_id = source["restore_plan_id"];
	        this.confirmed = source["confirmed"];
	    }
	}
	export class IPodStageRequest {
	    mount_path: string;
	    completed_files: string[];

	    static createFrom(source: any = {}) {
	        return new IPodStageRequest(source);
	    }

	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mount_path = source["mount_path"];
	        this.completed_files = source["completed_files"];
	    }
	}

}

