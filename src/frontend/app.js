// Security & Anti-DevTools Protection
document.addEventListener('contextmenu', e => e.preventDefault());
document.addEventListener('keydown', e => {
  if (e.key === 'F12') e.preventDefault();
  if (e.ctrlKey && e.shiftKey && 'IJC'.includes(e.key.toUpperCase())) e.preventDefault();
  if (e.ctrlKey && e.key.toLowerCase() === 'u') e.preventDefault();
});

setInterval(() => {
  const threshold = 160;
  const isDevToolsOpen =
    (window.outerWidth - window.innerWidth > threshold) ||
    (window.outerHeight - window.innerHeight > threshold);

  const dtwEl = document.getElementById('dtw');
  if (dtwEl) {
    dtwEl.style.display = isDevToolsOpen ? 'flex' : 'none';
  }
}, 1000);

// Global second-timer for real-time countdown updates
setInterval(() => {
  const now = Date.now() / 1000;

  // 1. Update cooldown & penalty countdowns
  document.querySelectorAll('.cooldown-wrapper').forEach(el => {
    const until = parseFloat(el.getAttribute('data-until'));
    const s = until - now;
    const txtEl = el.querySelector('.countdown-text');
    if (!txtEl) return;

    if (s <= 0) {
      el.innerHTML = `<span class="expired-tag" style="color:var(--emerald);font-weight:600">${t('lbl_expired')}</span>`;
      return;
    }

    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);

    let timeStr = '';
    if (h > 0) timeStr += `${h}h ${m}m ${sec}s`;
    else if (m > 0) timeStr += `${m}m ${sec}s`;
    else timeStr += `${sec}s`;

    txtEl.textContent = timeStr;
  });

  // 2. Update RPD reset countdown in My Account
  document.querySelectorAll('.rpd-reset-countdown').forEach(el => {
    const tomorrowTs = parseFloat(el.getAttribute('data-tomorrow-ts'));
    const diffMs = (tomorrowTs * 1000) - Date.now();

    if (diffMs <= 0) {
      el.textContent = '0h 0m 0s';
      return;
    }

    const hrs = Math.floor(diffMs / 3600000);
    const mins = Math.floor((diffMs % 3600000) / 60000);
    const secs = Math.floor((diffMs % 60000) / 1000);

    el.textContent = `${hrs}h ${mins}m ${secs}s`;
  });
}, 1000);

// Global State & UI Language/Theme Controllers
let _tok = sessionStorage.getItem('_rt') || null;
let _usr = null;
let _ch = {};
let _cur = null;
let _timer = null;
let _rawKeys = [];
let _rawAccounts = [];
let _statsData = null;
let _myStatsData = null;
let _rawEndpoints = null;
let _rawPenalties = null;
let _rawPools = null;
let _rawMyAcc = null;

let _lang = localStorage.getItem('_rl') || 'vi';
let _theme = localStorage.getItem('_rtm') || 'auto';

const CLR = [
  '#6366f1', // primary/indigo
  '#06b6d4', // cyan
  '#10b981', // emerald
  '#f59e0b', // amber
  '#f43f5e', // rose
  '#a855f7', // purple
  '#3b82f6'  // blue
];

const $ = id => document.getElementById(id);

// --- Translation Dictionary (TR) ---
const TR = {
  vi: {
    dtw_title: "Cảnh báo bảo mật",
    dtw_desc: "Phát hiện công cụ nhà phát triển (DevTools).<br>Vui lòng đóng DevTools để tiếp tục sử dụng hệ thống.",
    login_title: "Router API",
    login_desc: "Cockpit Dashboard — Restricted Access",
    login_label: "Khóa xác thực (Auth Key)",
    login_btn: "Đăng nhập vào Cockpit",
    login_footer: "🔒 Khóa xác thực không được lưu trữ sau khi đăng xuất",
    err_auth_key_required: "Vui lòng nhập auth key",
    btn_authenticating: "Đang xác thực...",
    err_server_connection: "Lỗi kết nối máy chủ: ",
    header_title: "Router API Cockpit",
    logout_btn: "Đăng xuất",
    lbl_refreshed: "↻ Đã cập nhật",
    nav_ov: "Tổng quan",
    nav_ks: "Gemini Keys",
    nav_ac: "Tài khoản con",
    nav_us: "Phân tích Token",
    nav_ep: "Cổng phụ (Endpoints)",
    nav_pe: "Hình phạt (Penalties)",
    nav_mu: "Cấu trúc Pool",
    nav_myacc: "Tài khoản của tôi",
    nav_myuse: "Usage của tôi",
    ov_title: "Tổng quan hệ thống",
    ov_sub: "Bảng điều khiển quản trị viên · cập nhật tự động mỗi 60 giây",
    ov_daily_stats: "Thống kê Token hàng ngày (30 ngày gần nhất)",
    ov_model_ratio: "Tỷ lệ sử dụng theo Model",
    ov_model_detail: "Chi tiết lượng tiêu thụ của Model",
    th_model: "Model",
    th_prompt_tokens: "Prompt Tokens",
    th_completion_tokens: "Completion Tokens",
    th_total_tokens: "Total Tokens",
    th_requests: "Lượt Requests",
    th_key_code: "Mã Key",
    th_tier: "Tier",
    th_status: "Trạng thái",
    th_today: "Hôm nay",
    th_total: "Tổng cộng",
    th_concurrency: "Concurrency",
    th_failures: "Lỗi liên tiếp",
    th_cooldown_time: "Thời gian mở khóa",
    th_account_name: "Tên tài khoản",
    th_rpm: "RPM",
    th_tpm: "TPM",
    th_rpd: "RPD",
    th_created: "Ngày tạo",
    th_ep_name: "Tên cổng",
    th_ep_url: "Địa chỉ Base URL",
    th_ep_models: "Các Model hỗ trợ",
    th_ep_fallback: "Chế độ Fallback",
    th_ep_updated: "Cập nhật cuối",
    th_pool_name: "Tên Pool ảo",
    th_pool_backing: "Model Gemini gánh tải thực tế",
    th_pool_rpm: "Hạn mức RPM Pool",
    th_pool_tpm: "Hạn mức TPM Pool",
    th_pool_status: "Trạng thái định tuyến",
    tip_model_alias: "Tên model Gemini backing thực tế",
    tip_prompt_tokens: "Tổng số token gửi lên (Prompt/Input)",
    tip_completion_tokens: "Tổng số token trả về (Completion/Output)",
    tip_total_tokens: "Tổng số token tiêu thụ (Prompt + Completion)",
    tip_requests: "Tổng số lượt request thành công",
    tip_key_code: "Định danh đã ẩn danh của API Key",
    tip_tier: "Phân cấp quyền truy cập của Key (Free/Premium/Admin)",
    tip_status: "Trạng thái hiện tại (Healthy: Hoạt động tốt, Cooldown: Hạn ngạch quá tải tạm thời, Degraded: Đang lỗi, Disabled: Bị tắt)",
    tip_today: "Số lượng request thành công trong ngày hôm nay",
    tip_total: "Tổng số request thành công lũy kế",
    tip_concurrency: "Số lượng request đang chạy song song tại thời điểm hiện tại",
    tip_failures: "Số lần bị lỗi liên tục gần nhất",
    tip_cooldown_time: "Thời gian đếm ngược còn lại trước khi Key được tự động mở khóa",
    tip_account_name: "Tên định danh của tài khoản",
    tip_account_status: "Active: Đang hoạt động, Disabled: Bị tạm ngưng",
    tip_rpm: "Requests Per Minute - Giới hạn request tối đa trên phút",
    tip_tpm: "Tokens Per Minute - Giới hạn token tối đa trên phút",
    tip_rpd: "Requests Per Day - Giới hạn request tối đa trên ngày",
    tip_created: "Thời điểm tài khoản được kích hoạt trên hệ thống",
    ks_title: "Gemini API Keys",
    ks_sub: "Quản lý và giám sát trạng thái sức khỏe, hạn mức của các khóa API Gemini",
    lbl_tier: "Tier",
    lbl_status: "Trạng thái",
    opt_all: "Tất cả",
    opt_admin: "Admin",
    opt_premium: "Premium",
    opt_free: "Free",
    opt_healthy: "Healthy",
    opt_cooldown: "Cooldown",
    opt_degraded: "Degraded",
    opt_disabled: "Disabled",
    ks_list_title: "Danh sách Gemini Keys",
    placeholder_search_keys: "Tìm kiếm API key...",
    placeholder_search_accounts: "Tìm kiếm tài khoản...",
    placeholder_auth_key: "sk-...",
    no_keys_found: "Không tìm thấy API Key nào phù hợp bộ lọc",
    group_admin: "ADMIN KEYS (Chạy không giới hạn)",
    group_premium: "PREMIUM KEYS (Hỗ trợ trả phí)",
    group_free: "FREE KEYS (Hạn ngạch miễn phí)",
    ac_title: "Quản lý tài khoản con",
    ac_sub: "Danh sách các tài khoản người dùng được quyền kết nối với Router API",
    opt_active: "Hoạt động (Active)",
    ac_list_title: "Danh sách tài khoản con",
    no_accounts_found: "Không tìm thấy tài khoản nào phù hợp bộ lọc",
    ac_card_total: "Tổng số tài khoản",
    ac_card_total_sub: "Tài khoản con đang hoạt động",
    ac_card_free: "Tài khoản Free",
    ac_card_free_sub: "Giới hạn RPM/TPM mặc định",
    ac_card_premium: "Tài khoản Premium",
    ac_card_premium_sub: "Băng thông RPM/TPM ưu tiên 1.5x",
    ac_card_admin: "Tài khoản Admin",
    ac_card_admin_sub: "Không bị giới hạn tốc độ",
    us_title: "Phân tích lượng sử dụng của Client",
    us_sub: "Xếp hạng lượng tiêu thụ token theo từng khóa tài khoản (30 ngày gần nhất)",
    us_rank_title: "Bảng xếp hạng tài khoản tiêu thụ nhiều nhất",
    ep_title: "Cổng phụ (Custom Endpoints)",
    ep_sub: "Các cổng kết nối custom dự phòng để chia sẻ tải với Gemini API chính thức",
    ep_list_title: "Danh sách cổng phụ",
    endpoints_count: "cổng phụ",
    no_endpoints: "Không cấu hình Custom Endpoints",
    pe_title: "Giám sát Hình phạt & Giảm cấp Key",
    pe_sub: "Danh sách các API Key đang bị hạ điểm ưu tiên do phát sinh lỗi",
    no_penalties: "Hiện tại không có API Key nào đang bị áp hình phạt.",
    lbl_error_reason: "Lý do",
    lbl_expires_after: "Hạn phạt",
    lbl_global: "Toàn cục (Global)",
    mu_title: "Cấu trúc Pool & Tiết kiệm Tài chính",
    mu_sub: "Phân tích hiệu suất định tuyến và số tiền tiết kiệm được so với việc gọi trực tiếp API Anthropic",
    mu_std_cost: "Claude 3.7 Cost (Ước tính)",
    mu_std_cost_sub: "Giá gốc ($3.00/1M In · $15.00/1M Out)",
    mu_cache_cost: "Claude 3.7 Caching Cost",
    mu_cache_cost_sub: "Mô phỏng cơ chế Caching của Anthropic",
    mu_gemini_cost: "Chi phí Gemini thực tế",
    mu_gemini_cost_sub: "Tính theo giá thực của backing models",
    mu_net_save: "Số tiền tiết kiệm ròng",
    mu_net_save_sub: "Lợi nhuận tài chính nhờ công nghệ Routing",
    mu_table_title: "Ánh xạ Pool ảo sang backing model Gemini thực tế",
    myacc_title: "Tài khoản của tôi",
    myacc_sub: "Thông tin chi tiết và hạn ngạch tài nguyên được phân bổ cho bạn",
    lbl_account_id: "ID tài khoản:",
    lbl_usage_limits: "Hạn mức sử dụng (Real-time)",
    lbl_usage_limits_sub: "Trạng thái hạn mức tài khoản hiện tại. RPM/TPM tự động làm mới liên tục theo phút trượt.",
    lbl_rpm_card: "Requests/Phút (RPM)",
    lbl_tpm_card: "Tokens/Phút (TPM)",
    lbl_rpd_card: "Requests/Ngày (RPD)",
    lbl_left: "Còn lại:",
    lbl_using: "Đang dùng:",
    lbl_reset: "Làm mới:",
    lbl_key_pools: "Khả dụng Pool Key (Tổng hợp Real-time)",
    lbl_key_pools_sub: "Băng thông tổng hợp từ toàn bộ API Key khả dụng trong hệ thống.",
    lbl_pool_rpd: "Yêu cầu còn lại (RPD)",
    lbl_pool_1h: "Tokens khả dụng (1h)",
    lbl_pool_12h: "Tokens khả dụng (12h)",
    lbl_pool_24h: "Tokens khả dụng (24h)",
    accounts_count: "tài khoản",
    keys_count: "khóa",
    myuse_title: "Thống kê cá nhân",
    myuse_sub: "Lịch sử và thống kê lưu lượng sử dụng cá nhân (30 ngày gần nhất)",
    myuse_daily_chart: "Thống kê sử dụng hàng ngày",
    myuse_model_chart: "Cơ cấu mô hình gọi",
    myuse_detail_title: "Chi tiết lượng tiêu thụ cá nhân",
    loading: "Đang tải dữ liệu...",
    load_error: "Lỗi tải dữ liệu",
    no_data: "Chưa có dữ liệu sử dụng",
    today: "Hôm nay",
    last_30_days: "30 ngày qua",
    savings_amt: "Số tiền tiết kiệm",
    active_models: "Active Models",
    requests_count: "requests",
    saved_vs_claude: "Saved vs Claude 3.7 Sonnet",
    gemini_supported: "Mô hình Gemini hỗ trợ",
    st_healthy: "Khỏe mạnh",
    st_cooldown: "Tạm khóa",
    st_disabled: "Đã tắt",
    st_degraded: "Suy giảm",
    ks_card_healthy_sub: "API Key hoạt động bình thường",
    ks_card_cooldown_sub: "API Key đang bị tạm khóa",
    ks_card_disabled_sub: "API Key đã bị vô hiệu hóa",
    ks_card_total: "Tổng số Key",
    ks_card_total_sub: "Đang hoạt động trong hệ thống",
    theme_auto: "Tự động",
    theme_dark: "Tối",
    theme_light: "Sáng",
    theme_sakura: "Sakura",
    th_score_reduction: "Giảm điểm",
    lbl_expired: "Hết hạn",
    err_invalid_auth_key: "Khóa xác thực không hợp lệ"
  },
  en: {
    dtw_title: "Security Alert",
    dtw_desc: "Developer tools (DevTools) detected.<br>Please close DevTools to continue using the system.",
    login_title: "Router API",
    login_desc: "Cockpit Dashboard — Restricted Access",
    login_label: "Authentication Key (Auth Key)",
    login_btn: "Login to Cockpit",
    login_footer: "🔒 Auth key is not stored after logging out",
    err_auth_key_required: "Please enter your auth key",
    btn_authenticating: "Authenticating...",
    err_server_connection: "Server connection error: ",
    header_title: "Router API Cockpit",
    logout_btn: "Logout",
    lbl_refreshed: "↻ Updated",
    nav_ov: "Overview",
    nav_ks: "Gemini Keys",
    nav_ac: "Accounts",
    nav_us: "Token Analysis",
    nav_ep: "Endpoints",
    nav_pe: "Penalties",
    nav_mu: "Pool Structure",
    nav_myacc: "My Account",
    nav_myuse: "My Usage",
    ov_title: "System Overview",
    ov_sub: "Admin dashboard · auto updates every 60 seconds",
    ov_daily_stats: "Daily Token Stats (Last 30 Days)",
    ov_model_ratio: "Usage Ratio by Model",
    ov_model_detail: "Model Consumption Details",
    th_model: "Model",
    th_prompt_tokens: "Prompt Tokens",
    th_completion_tokens: "Completion Tokens",
    th_total_tokens: "Total Tokens",
    th_requests: "Requests",
    th_key_code: "Key Code",
    th_tier: "Tier",
    th_status: "Status",
    th_today: "Today",
    th_total: "Total",
    th_concurrency: "Concurrency",
    th_failures: "Consec. Failures",
    th_cooldown_time: "Unlock Time",
    th_account_name: "Account Name",
    th_rpm: "RPM",
    th_tpm: "TPM",
    th_rpd: "RPD",
    th_created: "Created At",
    th_ep_name: "Endpoint Name",
    th_ep_url: "Base URL",
    th_ep_models: "Supported Models",
    th_ep_fallback: "Fallback",
    th_ep_updated: "Last Updated",
    th_pool_name: "Virtual Pool",
    th_pool_backing: "Gemini Backing Model",
    th_pool_rpm: "Pool RPM Limit",
    th_pool_tpm: "Pool TPM Limit",
    th_pool_status: "Routing Status",
    tip_model_alias: "Actual backing Gemini model alias",
    tip_prompt_tokens: "Total tokens sent (Prompt/Input)",
    tip_completion_tokens: "Total tokens returned (Completion/Output)",
    tip_total_tokens: "Total tokens consumed (Prompt + Completion)",
    tip_requests: "Total successful requests",
    tip_key_code: "Anonymized identifier of the API Key",
    tip_tier: "Access level of the Key (Free/Premium/Admin)",
    tip_status: "Current status (Healthy: Active, Cooldown: Rate limit exceeded, Degraded: Failing repeatedly, Disabled: Turned off)",
    tip_today: "Successful requests today",
    tip_total: "Cumulative successful requests",
    tip_concurrency: "Number of requests running concurrently right now",
    tip_failures: "Recent consecutive error count",
    tip_cooldown_time: "Time remaining until Key is automatically unlocked",
    tip_account_name: "Unique account identifier name",
    tip_account_status: "Active: In use, Disabled: Temporarily suspended",
    tip_rpm: "Requests Per Minute - Maximum requests allowed per minute",
    tip_tpm: "Tokens Per Minute - Maximum tokens allowed per minute",
    tip_rpd: "Requests Per Day - Maximum requests allowed per day",
    tip_created: "Date when account was activated on the system",
    ks_title: "Gemini API Keys",
    ks_sub: "Manage and monitor health status and quotas of Gemini API keys",
    lbl_tier: "Tier",
    lbl_status: "Status",
    opt_all: "All",
    opt_admin: "Admin",
    opt_premium: "Premium",
    opt_free: "Free",
    opt_healthy: "Healthy",
    opt_cooldown: "Cooldown",
    opt_degraded: "Degraded",
    opt_disabled: "Disabled",
    ks_list_title: "Gemini Keys List",
    placeholder_search_keys: "Search API key...",
    placeholder_search_accounts: "Search accounts...",
    placeholder_auth_key: "sk-...",
    no_keys_found: "No API Keys found matching filters",
    group_admin: "ADMIN KEYS (Unlimited usage)",
    group_premium: "PREMIUM KEYS (Paid backing tiers)",
    group_free: "FREE KEYS (Free tier quotas)",
    ac_title: "Sub-Accounts Management",
    ac_sub: "List of user accounts allowed to connect to Router API",
    opt_active: "Active",
    ac_list_title: "Sub-Accounts List",
    no_accounts_found: "No accounts found matching filters",
    ac_card_total: "Total Accounts",
    ac_card_total_sub: "Active sub-accounts",
    ac_card_free: "Free Accounts",
    ac_card_free_sub: "Default RPM/TPM limits",
    ac_card_premium: "Premium Accounts",
    ac_card_premium_sub: "1.5x prioritized priority bandwidth",
    ac_card_admin: "Admin Accounts",
    ac_card_admin_sub: "No rate limit checks applied",
    us_title: "Client Usage Analytics",
    us_sub: "Token consumption ranking by user account (Last 30 days)",
    us_rank_title: "Top Consuming Accounts Leaderboard",
    ep_title: "Custom Endpoints",
    ep_sub: "Backup endpoints configured to share load with standard Gemini API",
    ep_list_title: "Custom Endpoints List",
    endpoints_count: "endpoints",
    no_endpoints: "No Custom Endpoints configured",
    pe_title: "Active Penalties Board",
    pe_sub: "List of API keys temporarily deprioritized due to repeated failures",
    no_penalties: "No keys are currently penalized.",
    lbl_error_reason: "Error Reason",
    lbl_expires_after: "Expires After",
    lbl_global: "global (entire system)",
    mu_title: "Pool Structure & Savings Analysis",
    mu_sub: "Analyze routing performance and estimated financial savings compared to direct Anthropic calls",
    mu_std_cost: "Claude 3.7 Cost (Est.)",
    mu_std_cost_sub: "List Price ($3.00/1M In · $15.00/1M Out)",
    mu_cache_cost: "Claude 3.7 Caching Cost",
    mu_cache_cost_sub: "Simulating Anthropic's Caching",
    mu_gemini_cost: "Actual Gemini Cost",
    mu_gemini_cost_sub: "Based on real backing model prices",
    mu_net_save: "Net Financial Savings",
    mu_net_save_sub: "Financial profit generated via Router logic",
    mu_table_title: "Virtual Pools mapped to actual Gemini backing models",
    myacc_title: "My Account",
    myacc_sub: "Detailed info and resource quotas allocated to you",
    lbl_account_id: "Account ID:",
    lbl_usage_limits: "Usage Limits (Real-time)",
    lbl_usage_limits_sub: "Current quota allocation. RPM/TPM automatically reset on sliding 60s windows.",
    lbl_rpm_card: "Requests / Minute (RPM)",
    lbl_tpm_card: "Tokens / Minute (TPM)",
    lbl_rpd_card: "Requests / Day (RPD)",
    lbl_left: "Left:",
    lbl_using: "Using:",
    lbl_reset: "Reset:",
    lbl_key_pools: "Key Pools Bandwidth (Combined)",
    lbl_key_pools_sub: "Actual bandwidth aggregated from active API Keys ready to serve your requests.",
    lbl_pool_rpd: "Daily req left (RPD)",
    lbl_pool_1h: "Token in 1h window",
    lbl_pool_12h: "Token in 12h window",
    lbl_pool_24h: "Token in day window",
    accounts_count: "accounts",
    keys_count: "keys",
    myuse_title: "My Statistics",
    myuse_sub: "Personal usage history and model structure (Last 30 days)",
    myuse_daily_chart: "Daily Usage Stats",
    myuse_model_chart: "Call Ratio by Model",
    myuse_detail_title: "Personal Consumption Details",
    loading: "Loading data...",
    load_error: "Failed to load data",
    no_data: "No usage data found",
    today: "Today",
    last_30_days: "Last 30 Days",
    savings_amt: "Financial Savings",
    active_models: "Active Models",
    requests_count: "requests",
    saved_vs_claude: "Saved vs Claude 3.7 Sonnet",
    gemini_supported: "Supported Gemini models",
    st_healthy: "Healthy",
    st_cooldown: "Cooldown",
    st_disabled: "Disabled",
    st_degraded: "Degraded",
    ks_card_healthy_sub: "API keys working healthy",
    ks_card_cooldown_sub: "Keys temporarily frozen",
    ks_card_disabled_sub: "Keys completely disabled",
    ks_card_total: "Total keys",
    ks_card_total_sub: "Loaded into the router",
    theme_auto: "Auto",
    theme_dark: "Dark",
    theme_light: "Light",
    theme_sakura: "Sakura",
    th_score_reduction: "Score Reduction",
    lbl_expired: "Expired",
    err_invalid_auth_key: "Invalid authentication key"
  },
  ja: {
    dtw_title: "セキュリティ警告",
    dtw_desc: "開発者ツール（DevTools）が検出されました。<br>システムを引き続き使用するには、DevToolsを閉じてください。",
    login_title: "Router API",
    login_desc: "Cockpit Dashboard — Restricted Access",
    login_label: "認証キー (Auth Key)",
    login_btn: "Cockpit にログイン",
    login_footer: "🔒 ログアウト後、認証キーは保存されません",
    err_auth_key_required: "認証キーを入力してください",
    btn_authenticating: "認証中...",
    err_server_connection: "サーバー接続エラー: ",
    header_title: "Router API Cockpit",
    logout_btn: "ログアウト",
    lbl_refreshed: "↻ 更新完了",
    nav_ov: "ダッシュボード",
    nav_ks: "Gemini キー",
    nav_ac: "サブアカウント",
    nav_us: "トークン分析",
    nav_ep: "カスタム接続",
    nav_pe: "ペナルティ履歴",
    nav_mu: "プール構造",
    nav_myacc: "マイアカウント",
    nav_myuse: "マイ統計",
    ov_title: "システム概要",
    ov_sub: "管理者ダッシュボード · 60秒ごとに自動更新",
    ov_daily_stats: "日次トークン統計 (過去30日間)",
    ov_model_ratio: "モデル別使用比率",
    ov_model_detail: "モデル消費の詳細",
    th_model: "モデル",
    th_prompt_tokens: "入力トークン",
    th_completion_tokens: "出力トークン",
    th_total_tokens: "合計トークン",
    th_requests: "リクエスト数",
    th_key_code: "キーコード",
    th_tier: "ティア",
    th_status: "ステータス",
    th_today: "本日",
    th_total: "累計",
    th_concurrency: "並行処理数",
    th_failures: "連続エラー",
    th_cooldown_time: "ロック解除時間",
    th_account_name: "アカウント名",
    th_rpm: "RPM",
    th_tpm: "TPM",
    th_rpd: "RPD",
    th_created: "作成日時",
    th_ep_name: "エンドポイント名",
    th_ep_url: "ベース URL",
    th_ep_models: "サポートモデル",
    th_ep_fallback: "代替接続",
    th_ep_updated: "最終更新",
    th_pool_name: "仮想プール",
    th_pool_backing: "バックエンド Gemini モデル",
    th_pool_rpm: "プール RPM 制限",
    th_pool_tpm: "プール TPM 制限",
    th_pool_status: "ルーティング状況",
    tip_model_alias: "実際のバックエンド Gemini モデル名",
    tip_prompt_tokens: "送信されたトークンの合計 (プロンプト/入力)",
    tip_completion_tokens: "返されたトークンの合計 (完了/出力)",
    tip_total_tokens: "消費された合計トークン (プロンプト + 完了)",
    tip_requests: "成功したリクエストの総数",
    tip_key_code: "APIキーの匿名化された識別子",
    tip_tier: "キーのアクセスレベル (Free/Premium/Admin)",
    tip_status: "現在のステータス (Healthy: 正常稼働中, Cooldown: 一時的な速度制限中, Degraded: 連続エラー発生中, Disabled: 無効化中)",
    tip_today: "本日の成功リクエスト数",
    tip_total: "これまでの成功リクエスト総数",
    tip_concurrency: "現在同時に実行されているリクエスト数",
    tip_failures: "直近 of 連続エラー回数",
    tip_cooldown_time: "キーが自動的にロック解除されるまでの残り時間",
    tip_account_name: "一意のアカウント識別名",
    tip_account_status: "Active: 稼働中, Disabled: 一時停止中",
    tip_rpm: "Requests Per Minute - 1分あたりの最大リクエスト数",
    tip_tpm: "Tokens Per Minute - 1分あたりの最大トークン数",
    tip_rpd: "Requests Per Day - 1日あたりの最大リクエスト数",
    tip_created: "アカウントがシステム上で有効化された日時",
    ks_title: "Gemini API キー",
    ks_sub: "Gemini API キーの稼働状態と割り当て枠の管理と監視",
    lbl_tier: "ティア",
    lbl_status: "ステータス",
    opt_all: "すべて",
    opt_admin: "Admin",
    opt_premium: "Premium",
    opt_free: "Free",
    opt_healthy: "Healthy",
    opt_cooldown: "Cooldown",
    opt_degraded: "Degraded",
    opt_disabled: "Disabled",
    ks_list_title: "Gemini キー一覧",
    placeholder_search_keys: "APIキーを検索...",
    placeholder_search_accounts: "アカウントを検索...",
    placeholder_auth_key: "sk-...",
    no_keys_found: "フィルターに一致するAPIキーが見つかりません",
    group_admin: "ADMIN KEYS (実行制限なし)",
    group_premium: "PREMIUM KEYS (有料ティアキー)",
    group_free: "FREE KEYS (無料制限枠キー)",
    ac_title: "サブアカウント管理",
    ac_sub: "Router APIへの接続を許可されたユーザーアカウントの一覧",
    opt_active: "アクティブ",
    ac_list_title: "サブアカウント一覧",
    no_accounts_found: "フィルターに一致するアカウントが見つかりません",
    ac_card_total: "アカウント総数",
    ac_card_total_sub: "アクティブなサブアカウント",
    ac_card_free: "Free アカウント",
    ac_card_free_sub: "デフォルトの RPM/TPM 制限",
    ac_card_premium: "Premium アカウント",
    ac_card_premium_sub: "1.5倍の優先割り当て帯域幅",
    ac_card_admin: "Admin アカウント",
    ac_card_admin_sub: "速度制限チェックは適用されません",
    us_title: "クライアント使用状況分析",
    us_sub: "ユーザーアカウントごとのトークン消費量ランキング (過去30日間)",
    us_rank_title: "高消費アカウントリーダーボード",
    ep_title: "外部接続エンドポイント",
    ep_sub: "Gemini公式APIと負荷を共有するために構成されたカスタムバックアップ接続",
    ep_list_title: "接続先一覧",
    endpoints_count: "個のエンドポイント",
    no_endpoints: "カスタムエンドポイントは設定されていません",
    pe_title: "ペナルティ中のキー一覧",
    pe_sub: "連続してエラーが発生したため、一時的に優先度が下げられているAPIキーの一覧",
    no_penalties: "現在ペナルティ中のキーはありません。",
    lbl_error_reason: "エラーの原因",
    lbl_expires_after: "残り期限",
    lbl_global: "システム全体",
    mu_title: "プール構造とコスト節約分析",
    mu_sub: "Anthropic APIを直接呼び出す場合と比較した、ルーティングパフォーマンスと推定コスト節約額の分析",
    mu_std_cost: "Claude 3.7 コスト (推定)",
    mu_std_cost_sub: "定価 ($3.00/1M In · $15.00/1M Out)",
    mu_cache_cost: "Claude 3.7 キャッシングコスト",
    mu_cache_cost_sub: "Anthropicのキャッシュメカニズムのシミュレーション",
    mu_gemini_cost: "実際の Gemini コスト",
    mu_gemini_cost_sub: "バックエンドモデルの実際の価格に基づく",
    mu_net_save: "正味の節約額",
    mu_net_save_sub: "ルーティング技術によって生み出された財務的利益",
    mu_table_title: "実際のGeminiモデルにマッピングされた仮想プール",
    myacc_title: "マイアカウント",
    myacc_sub: "あなたに割り当てられた詳細情報とリソース割り当て制限",
    lbl_account_id: "アカウントID:",
    lbl_usage_limits: "使用量制限 (リアルタイム)",
    lbl_usage_limits_sub: "現在のアカウント制限ステータス。RPM/TPMは60秒のスライディングウィンドウで自動的にリセットされます。",
    lbl_rpm_card: "リクエスト / 分 (RPM)",
    lbl_tpm_card: "トークン / 分 (TPM)",
    lbl_rpd_card: "リクエスト / 日 (RPD)",
    lbl_left: "残り枠:",
    lbl_using: "使用中:",
    lbl_reset: "リセットまで:",
    lbl_key_pools: "キープール利用可能帯域幅 (実質合計)",
    lbl_key_pools_sub: "リクエストを処理するために準備されている、すべてのアクティブなAPIキーから集計された実際の帯域幅。",
    lbl_pool_rpd: "残りのデイリー要求 (RPD)",
    lbl_pool_1h: "1時間枠内のトークン",
    lbl_pool_12h: "12時間枠内のトークン",
    lbl_pool_24h: "本日中のトークン",
    accounts_count: "アカウント",
    keys_count: "キー",
    myuse_title: "個人統計",
    myuse_sub: "過去30日間の個人使用履歴とモデル構造",
    myuse_daily_chart: "日次使用統計",
    myuse_model_chart: "モデル別呼び出し比率",
    myuse_detail_title: "個人消費の詳細",
    loading: "データを読み込み中...",
    load_error: "データの読み込みに失敗しました",
    no_data: "使用履歴はありません",
    today: "本日",
    last_30_days: "過去30日間",
    savings_amt: "コスト節約額",
    active_models: "有効なモデル",
    requests_count: "回リクエスト",
    saved_vs_claude: "Claude 3.7 Sonnet に対する節約",
    gemini_supported: "サポート対象の Gemini モデル",
    st_healthy: "Healthy",
    st_cooldown: "Cooldown",
    st_disabled: "Disabled",
    st_degraded: "Degraded",
    ks_card_healthy_sub: "正常稼働中のキー",
    ks_card_cooldown_sub: "一時停止中のキー",
    ks_card_disabled_sub: "無効化されたキー",
    ks_card_total: "キー総数",
    ks_card_total_sub: "ルーターに読み込まれました",
    theme_auto: "自動",
    theme_dark: "ダーク",
    theme_light: "ライト",
    theme_sakura: "サクラ",
    th_score_reduction: "減少スコア",
    lbl_expired: "期限切れ",
    err_invalid_auth_key: "無効な認証キー"
  }
};

function t(key) {
  return TR[_lang]?.[key] || TR['vi']?.[key] || key;
}

function applyLanguage(lang) {
  _lang = lang;
  localStorage.setItem('_rl', lang);
  
  // Clear the static DOM references to force a complete re-render when language switches
  const ovCards = $('ov-cards'); if (ovCards) ovCards.innerHTML = '';
  const ksCards = $('ks-cards'); if (ksCards) ksCards.innerHTML = '';
  const acCards = $('ac-cards'); if (acCards) acCards.innerHTML = '';
  const myaccCt = $('myacc-ct'); if (myaccCt) myaccCt.innerHTML = '';
  const myuseCards = $('myuse-cards'); if (myuseCards) myuseCards.innerHTML = '';
  
  // Update custom select display text
  const langTextMap = {
    vi: 'Tiếng Việt',
    en: 'English',
    ja: '日本語'
  };
  const selectedLangText = $('selected-lang-text');
  if (selectedLangText) {
    selectedLangText.textContent = langTextMap[lang] || lang;
  }

  // Highlight active item in custom select
  document.querySelectorAll('#lang-options .option-item').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-value') === lang);
  });
  
  // Update data-i18n elements
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.innerHTML = t(key);
  });
  
  // Update tooltips
  document.querySelectorAll('[data-tip-i18n]').forEach(el => {
    const key = el.getAttribute('data-tip-i18n');
    el.setAttribute('data-tip', t(key));
  });
  
  // Update placeholders
  const ksSearch = $('ks-search');
  if (ksSearch) ksSearch.placeholder = t('placeholder_search_keys');
  const acSearch = $('ac-search');
  if (acSearch) acSearch.placeholder = t('placeholder_search_accounts');
  const ki = $('ki');
  if (ki) ki.placeholder = t('placeholder_auth_key');

  // Update selected theme text translation in case language changed
  const selectedThemeText = $('selected-theme-text');
  if (selectedThemeText) {
    const themeTextMap = {
      auto: t('theme_auto'),
      dark: t('theme_dark'),
      light: t('theme_light'),
      sakura: t('theme_sakura')
    };
    selectedThemeText.textContent = themeTextMap[_theme] || t('theme_auto');
  }
  
  // Rerender active tab to translate dynamic text
  if (_cur) {
    loadTab(_cur, false);
  }
}

function applyTheme(theme) {
  _theme = theme;
  const root = document.documentElement;
  root.classList.remove('theme-light', 'theme-sakura');
  
  let activeTheme = theme;
  if (theme === 'auto') {
    const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    activeTheme = isDark ? 'dark' : 'light';
  }
  
  if (activeTheme === 'light') {
    root.classList.add('theme-light');
  } else if (activeTheme === 'sakura') {
    root.classList.add('theme-sakura');
  }
  
  localStorage.setItem('_rtm', theme);

  // Update custom select display text
  const selectedThemeText = $('selected-theme-text');
  if (selectedThemeText) {
    const themeTextMap = {
      auto: t('theme_auto'),
      dark: t('theme_dark'),
      light: t('theme_light'),
      sakura: t('theme_sakura')
    };
    selectedThemeText.textContent = themeTextMap[theme] || t('theme_auto');
  }

  // Copy icon to trigger
  const selectedThemeIconWrapper = $('selected-theme-icon-wrapper');
  if (selectedThemeIconWrapper) {
    const activeOption = document.querySelector(`#theme-options .option-item[data-value="${theme}"]`);
    if (activeOption) {
      const iconSvg = activeOption.querySelector('svg');
      if (iconSvg) {
        selectedThemeIconWrapper.innerHTML = iconSvg.outerHTML;
      }
    }
  }

  // Highlight active item in custom select
  document.querySelectorAll('#theme-options .option-item').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-value') === theme);
  });
  
  // Repaint charts
  updateChartColors();
}

function updateChartColors() {
  if (_cur === 'ov') {
    loadOv();
  } else if (_cur === 'myuse') {
    loadMyUse();
  }
}

// Custom Select Dropdown Toggle
window.toggleDropdown = function(event, dropdownId) {
  event.stopPropagation();
  const target = document.getElementById(dropdownId);
  if (!target) return;
  const container = target.parentElement;
  const isOpen = container.classList.contains('open');
  
  // Close all other custom dropdowns
  document.querySelectorAll('.custom-select').forEach(el => {
    el.classList.remove('open');
  });
  
  if (!isOpen) {
    container.classList.add('open');
  }
};

// Close dropdowns on clicking outside
document.addEventListener('click', () => {
  document.querySelectorAll('.custom-select').forEach(el => {
    el.classList.remove('open');
  });
});

// Bind Global Switcher Actions for inline html handlers
window.changeLang = function(lang) {
  if (lang !== _lang) {
    localStorage.setItem('_rl', lang);
    window.location.reload();
  }
};

window.changeTheme = function(theme) {
  applyTheme(theme);
};

// Listen to system theme change if auto mode is selected
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (localStorage.getItem('_rtm') === 'auto') {
    applyTheme('auto');
  }
});

// Formatting & UI Utility helpers
function fmt(v) {
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return (v || 0).toLocaleString();
}

function fmtD(ts) {
  if (!ts) return '—';
  const locale = _lang === 'vi' ? 'vi-VN' : _lang === 'ja' ? 'ja-JP' : 'en-US';
  return new Date(ts * 1000).toLocaleDateString(locale);
}

function relt(ts) {
  const s = ts - Date.now() / 1000;
  const isVi = _lang === 'vi';
  const isJa = _lang === 'ja';

  if (s <= 0) return `<span class="expired-tag">${t('lbl_expired')}</span>`;

  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);

  let timeStr = '';
  if (h > 0) timeStr += `${h}h ${m}m ${sec}s`;
  else if (m > 0) timeStr += `${m}m ${sec}s`;
  else timeStr += `${sec}s`;

  return `
    <span class="cooldown-wrapper" data-until="${ts}">
      <svg class="clock-anim" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"></circle>
        <polyline points="12 6 12 12 16 14" class="clock-hand"></polyline>
      </svg>
      <span class="countdown-text">${timeStr}</span>
    </span>
  `;
}

function tBadge(tier) {
  const m = {
    'free': 'tb-free',
    'premium': 'tb-premium',
    'admin': 'tb-admin'
  };
  const label = tier || 'free';
  return `<span class="tb ${m[label] || 'tb-free'}">${t('opt_' + label)}</span>`;
}

function sBadge(e) {
  return e 
    ? `<span class="b bg"><span class="hd hg"></span>${t('opt_active')}</span>` 
    : `<span class="b br"><span class="hd hr"></span>${t('opt_disabled')}</span>`;
}

function spHtml() {
  return `<div class="ld"><div class="sp"></div>${t('loading')}</div>`;
}

function emptyRow(cols, msg) {
  return `<tr><td colspan="${cols}" style="text-align:center;color:var(--text-dark);padding:30px">${msg}</td></tr>`;
}

function animateUpdateText(id, newText) {
  const el = $(id);
  if (!el) return;
  if (el.textContent !== newText) {
    el.textContent = newText;
    el.classList.remove('num-changed');
    void el.offsetWidth; // trigger reflow
    el.classList.add('num-changed');
  }
}

function updateProgressBar(id, pct, color) {
  const el = $(id);
  if (!el) return;
  el.style.width = pct + '%';
  el.style.background = color;
}

// API Connection wrapper
async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: {
      'X-Dashboard-Token': _tok || '',
      ...(opts.headers || {})
    }
  });
  if (r.status === 401) {
    doLogout();
    return null;
  }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

// Eye toggle login password
function toggleEye() {
  const i = $('ki');
  const b = $('eb');
  if (i.type === 'password') {
    i.type = 'text';
    b.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
        <line x1="1" y1="1" x2="23" y2="23"></line>
      </svg>
    `;
  } else {
    i.type = 'password';
    b.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
        <circle cx="12" cy="12" r="3"></circle>
      </svg>
    `;
  }
}

// Bind Enter Key on Login
const kiInput = $('ki');
if (kiInput) {
  kiInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });
}

// Authentication Actions
async function doLogin() {
  const key = $('ki').value.trim();
  const err = $('lerr');
  const btn = $('lbtn');
  if (!key) {
    showErr(t('err_auth_key_required'));
    return;
  }
  btn.disabled = true;
  btn.textContent = t('btn_authenticating');
  err.style.display = 'none';
  try {
    const r = await fetch('/dashboard/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auth_key: key })
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showErr(d.error || t('err_invalid_auth_key'));
      return;
    }
    const data = await r.json();
    _tok = data.token;
    _usr = { name: data.name, tier: data.tier };
    sessionStorage.setItem('_rt', _tok);
    $('ki').value = '';
    enterApp();
  } catch (e) {
    showErr(t('err_server_connection') + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = t('login_btn');
  }
}

function showErr(m) {
  const e = $('lerr');
  e.textContent = m;
  e.style.display = 'block';
}

function doLogout() {
  sessionStorage.removeItem('_rt');
  _tok = null;
  _usr = null;
  _rawKeys = [];
  _rawAccounts = [];
  _statsData = null;
  _myStatsData = null;
  _rawEndpoints = null;
  _rawPenalties = null;
  _rawPools = null;
  _rawMyAcc = null;
  if (_timer) clearInterval(_timer);
  _timer = null;
  Object.values(_ch).forEach(c => c && c.destroy && c.destroy());
  _ch = {};
  $('app').style.display = 'none';
  $('ls').style.display = 'flex';
}

// Session Initializer
async function enterApp() {
  if (_tok && !_usr) {
    try {
      const me = await api('/dashboard/me');
      if (!me) return;
      _usr = me;
    } catch (e) {
      doLogout();
      return;
    }
  }
  $('ls').style.display = 'none';
  $('app').style.display = 'flex';
  $('uname').textContent = _usr.name;
  
  const tb = $('utier');
  tb.textContent = _usr.tier;
  tb.className = 'tb tb-' + (_usr.tier || 'free');
  
  const isAdmin = _usr.tier === 'admin';
  document.querySelectorAll('.admin-only').forEach(el => el.classList.toggle('hide', !isAdmin));
  document.querySelectorAll('.user-only').forEach(el => el.classList.toggle('hide', isAdmin));
  
  go(isAdmin ? 'ov' : 'myacc');
  
  if (_timer) clearInterval(_timer);
  _timer = setInterval(() => {
    if (_cur) loadTab(_cur, true);
    showRefresh();
  }, 10000);
}

function showRefresh() {
  $('rfind').innerHTML = `<span style="color:var(--emerald);font-weight:600">${t('lbl_refreshed')}</span>`;
  setTimeout(() => $('rfind').innerHTML = '', 3000);
}

// Navigation router
function go(name) {
  document.querySelectorAll('.tp').forEach(p => p.classList.remove('on'));
  document.querySelectorAll('.ni').forEach(n => n.classList.remove('on'));
  
  const p = $('tp-' + name);
  const n = $('nv-' + name);
  if (p) p.classList.add('on');
  if (n) n.classList.add('on');
  
  _cur = name;
  loadTab(name, true);
}

function loadTab(n, force = false) {
  const tabs = {
    ov: loadOv,
    ks: loadKs,
    ac: loadAc,
    us: loadUs,
    ep: loadEp,
    pe: loadPe,
    myacc: loadMyAcc,
    myuse: loadMyUse,
    mu: loadMu
  };
  tabs[n]?.(force);
}

// Chart.js helpers
function mkLine(ctx, labels, ds) {
  const style = window.getComputedStyle(document.documentElement);
  const textMuted = style.getPropertyValue('--text-muted').trim() || '#9ca3af';
  const textDark = style.getPropertyValue('--text-dark').trim() || '#6b7280';
  const border = style.getPropertyValue('--border').trim() || 'rgba(255,255,255,.03)';

  return new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: ds },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: {
            color: textMuted,
            boxWidth: 8,
            padding: 8,
            font: { size: 10, family: 'Inter' }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: textDark, maxTicksLimit: 7, maxRotation: 30, font: { size: 9, family: 'Inter' } },
          grid: { color: border }
        },
        y: {
          ticks: { color: textDark, callback: v => fmt(v), font: { size: 9, family: 'Inter' } },
          grid: { color: border }
        }
      }
    }
  });
}

function mkDonut(ctx, labels, data) {
  const style = window.getComputedStyle(document.documentElement);
  const textMuted = style.getPropertyValue('--text-muted').trim() || '#9ca3af';

  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: CLR.slice(0, data.length),
        borderWidth: 0,
        hoverOffset: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: textMuted,
            boxWidth: 8,
            padding: 6,
            font: { size: 10, family: 'Inter' }
          }
        }
      },
      cutout: '70%'
    }
  });
}

function buildDS(d) {
  const labels = [...new Set(d.map(x => x.d))].sort();
  const models = [...new Set(d.map(x => x.model_alias))];
  return {
    labels,
    datasets: models.map((m, i) => ({
      label: m,
      data: labels.map(l => {
        const f = d.find(x => x.d === l && x.model_alias === m);
        return f ? f.t : 0;
      }),
      borderColor: CLR[i % CLR.length],
      backgroundColor: CLR[i % CLR.length] + '0f',
      fill: true,
      tension: 0.4,
      pointRadius: 1,
      borderWidth: 2
    }))
  };
}

function updateLineChart(chart, labels, datasets) {
  chart.data.labels = labels;

  datasets.forEach((newDs, i) => {
    if (chart.data.datasets[i]) {
      chart.data.datasets[i].data = newDs.data;
      chart.data.datasets[i].label = newDs.label;
      chart.data.datasets[i].borderColor = newDs.borderColor;
      chart.data.datasets[i].backgroundColor = newDs.backgroundColor;
    } else {
      chart.data.datasets.push(newDs);
    }
  });

  if (chart.data.datasets.length > datasets.length) {
    chart.data.datasets.splice(datasets.length);
  }

  chart.update();
}

function updateDonutChart(chart, labels, data) {
  chart.data.labels = labels;
  chart.data.datasets[0].data = data;
  chart.data.datasets[0].backgroundColor = CLR.slice(0, data.length);
  chart.update();
}

// Financial Savings tickers
function animateSavings(targetVal, prefix) {
  const el = $(prefix + '-ticking-savings');
  if (!el) return;

  let startVal = 0.0;
  const currentText = el.textContent;
  if (currentText && currentText.startsWith('$')) {
    const parsed = parseFloat(currentText.substring(1));
    if (!isNaN(parsed) && parsed > 0) {
      startVal = parsed;
    }
  }

  if (Math.abs(targetVal - startVal) < 0.0001) {
    el.textContent = `$${targetVal.toFixed(4)}`;
    const key = 'savingsInterval_' + prefix;
    if (window[key]) clearInterval(window[key]);
    window[key] = setInterval(() => {
      targetVal += Math.random() * 0.0001;
      el.textContent = `$${targetVal.toFixed(4)}`;
    }, 3500);
    return;
  }

  let currentVal = startVal;
  const duration = 1200;
  const start = performance.now();

  function update(time) {
    const elapsed = time - start;
    const progress = Math.min(elapsed / duration, 1);
    currentVal = startVal + (targetVal - startVal) * progress;
    el.textContent = `$${currentVal.toFixed(4)}`;

    if (progress < 1) {
      requestAnimationFrame(update);
    } else {
      el.textContent = `$${targetVal.toFixed(4)}`;
      const key = 'savingsInterval_' + prefix;
      if (window[key]) clearInterval(window[key]);
      window[key] = setInterval(() => {
        targetVal += Math.random() * 0.0001;
        el.textContent = `$${targetVal.toFixed(4)}`;
      }, 3500);
    }
  }
  requestAnimationFrame(update);
}

function statsCards(s, d, savings, prefix) {
  const tot = s.reduce((a, b) => a + (b.t || 0), 0);
  const req = s.reduce((a, b) => a + (b.req || 0), 0);
  const now = new Date();
  const td = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  
  const tdd = d.filter(x => x.d === td);
  const tdt = tdd.reduce((a, b) => a + (b.t || 0), 0);
  const tdr = tdd.reduce((a, b) => a + (b.req || 0), 0);
  const sav = savings ? (savings.net_savings || 0) : 0;
  
  setTimeout(() => animateSavings(sav, prefix), 100);
  
  return `
    <div class="sc cp">
      <div class="sc-header">
        <div class="sc-lb">${t('today')}</div>
        <div class="sc-icon" style="color:var(--primary)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
            <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="16" y1="2" x2="16" y2="6"></line>
            <line x1="8" y1="2" x2="8" y2="6"></line>
            <line x1="3" y1="10" x2="21" y2="10"></line>
          </svg>
        </div>
      </div>
      <div class="sc-v" id="${prefix}-card-today-tokens">${fmt(tdt)}</div>
      <div class="sc-s" id="${prefix}-card-today-reqs">${tdr.toLocaleString()} ${t('requests_count')}</div>
    </div>
    <div class="sc cc">
      <div class="sc-header">
        <div class="sc-lb">${t('last_30_days')}</div>
        <div class="sc-icon" style="color:var(--cyan)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
            <line x1="18" y1="20" x2="18" y2="10"></line>
            <line x1="12" y1="20" x2="12" y2="4"></line>
            <line x1="6" y1="20" x2="6" y2="14"></line>
          </svg>
        </div>
      </div>
      <div class="sc-v" id="${prefix}-card-30d-tokens">${fmt(tot)}</div>
      <div class="sc-s" id="${prefix}-card-30d-reqs">${req.toLocaleString()} ${t('requests_count')}</div>
    </div>
    <div class="sc cg">
      <div class="sc-header">
        <div class="sc-lb">${t('savings_amt')}</div>
        <div class="sc-icon" style="color:var(--emerald)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
            <line x1="12" y1="1" x2="12" y2="23"></line>
            <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path>
          </svg>
        </div>
      </div>
      <div class="sc-v" id="${prefix}-ticking-savings" style="color:var(--emerald)">$0.0000</div>
      <div class="sc-s">${t('saved_vs_claude')}</div>
    </div>
    <div class="sc ca">
      <div class="sc-header">
        <div class="sc-lb">${t('active_models')}</div>
        <div class="sc-icon" style="color:var(--amber)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
            <line x1="8" y1="21" x2="16" y2="21"></line>
            <line x1="12" y1="17" x2="12" y2="21"></line>
          </svg>
        </div>
      </div>
      <div class="sc-v" id="${prefix}-card-active-models">${s.length}</div>
      <div class="sc-s">${t('gemini_supported')}</div>
    </div>
  `;
}

function statsTable(s) {
  return s.map((r, i) => `
    <tr class="tr-anim" style="animation-delay: ${i * 0.03}s">
      <td><strong>${r.model_alias}</strong></td>
      <td>${fmt(r.p || 0)}</td>
      <td>${fmt(r.c || 0)}</td>
      <td style="font-weight:700">${fmt(r.t || 0)}</td>
      <td>${(r.req || 0).toLocaleString()}</td>
    </tr>
  `).join('') || emptyRow(5, t('no_data'));
}

// ── Tab Loaders ──

// [ADMIN] Overview
async function loadOv(force = false) {
  try {
    if (force || !_statsData) {
      const data = await api('/api/stats?days=30');
      if (!data) return;
      _statsData = data;
    }
    const data = _statsData;
    const s = data.summary || [];
    const d = data.daily || [];

    const tot = s.reduce((a, b) => a + (b.t || 0), 0);
    const req = s.reduce((a, b) => a + (b.req || 0), 0);
    const now = new Date();
    const td = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const tdd = d.filter(x => x.d === td);
    const tdt = tdd.reduce((a, b) => a + (b.t || 0), 0);
    const tdr = tdd.reduce((a, b) => a + (b.req || 0), 0);
    const sav = data.savings ? (data.savings.net_savings || 0) : 0;

    if ($('ov-card-today-tokens')) {
      animateUpdateText('ov-card-today-tokens', fmt(tdt));
      animateUpdateText('ov-card-today-reqs', `${tdr.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('ov-card-30d-tokens', fmt(tot));
      animateUpdateText('ov-card-30d-reqs', `${req.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('ov-card-active-models', s.length.toString());
      setTimeout(() => animateSavings(sav, 'ov'), 100);
    } else {
      $('ov-cards').innerHTML = statsCards(s, d, data.savings, 'ov');
    }

    $('ov-tb').innerHTML = statsTable(s);

    const ld = buildDS(d);
    if (_ch.day && ld.labels.length) {
      updateLineChart(_ch.day, ld.labels, ld.datasets);
    } else if (ld.labels.length) {
      if (_ch.day) _ch.day.destroy();
      _ch.day = mkLine($('cDay').getContext('2d'), ld.labels, ld.datasets);
    }

    if (_ch.mod && s.length) {
      updateDonutChart(_ch.mod, s.map(x => x.model_alias), s.map(x => x.t || 0));
    } else if (s.length) {
      if (_ch.mod) _ch.mod.destroy();
      _ch.mod = mkDonut($('cMod').getContext('2d'), s.map(x => x.model_alias), s.map(x => x.t || 0));
    }
  } catch (e) {
    $('ov-cards').innerHTML = `<div class="sc cr"><div class="sc-lb">${t('load_error')}</div><div class="sc-s">${e.message}</div></div>`;
  }
}

// [ADMIN] Gemini Keys
async function loadKs(force = false) {
  if (!_rawKeys || !_rawKeys.length) {
    $('ks-tb').innerHTML = `<tr><td colspan="8">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !_rawKeys || !_rawKeys.length) {
      const data = await api('/dashboard/keys');
      if (!data) return;
      _rawKeys = data.keys || [];
    }
    const now = Date.now() / 1000;
    const healthy = _rawKeys.filter(k => k.enabled && !k.frozen).length;
    const frozen = _rawKeys.filter(k => k.frozen).length;
    const disabled = _rawKeys.filter(k => !k.enabled).length;

    if ($('ks-card-healthy')) {
      animateUpdateText('ks-card-healthy', healthy.toString());
      animateUpdateText('ks-card-cooldown', frozen.toString());
      animateUpdateText('ks-card-disabled', disabled.toString());
      animateUpdateText('ks-card-total', _rawKeys.length.toString());
    } else {
      $('ks-cards').innerHTML = `
        <div class="sc cg">
          <div class="sc-lb">${t('st_healthy')}</div>
          <div class="sc-v num-animate" id="ks-card-healthy">${healthy}</div>
          <div class="sc-s">${t('ks_card_healthy_sub')}</div>
        </div>
        <div class="sc ca">
          <div class="sc-lb">${t('st_cooldown')}</div>
          <div class="sc-v num-animate" id="ks-card-cooldown">${frozen}</div>
          <div class="sc-s">${t('ks_card_cooldown_sub')}</div>
        </div>
        <div class="sc cr">
          <div class="sc-lb">${t('st_disabled')}</div>
          <div class="sc-v num-animate" id="ks-card-disabled">${disabled}</div>
          <div class="sc-s">${t('ks_card_disabled_sub')}</div>
        </div>
        <div class="sc cp">
          <div class="sc-lb">${t('ks_card_total')}</div>
          <div class="sc-v num-animate" id="ks-card-total">${_rawKeys.length}</div>
          <div class="sc-s">${t('ks_card_total_sub')}</div>
        </div>
      `;
    }

    const nb = $('nbf');
    nb.textContent = frozen;
    nb.style.display = frozen ? 'inline' : 'none';
    
    renderFilteredKeys();
  } catch (e) {
    $('ks-tb').innerHTML = `<tr><td colspan="8" style="color:var(--rose);padding:16px">${e.message}</td></tr>`;
  }
}

function renderFilteredKeys() {
  const query = ($('ks-search')?.value || '').toLowerCase().trim();
  const tier = $('ks-filter-tier')?.value || 'all';
  const status = $('ks-filter-status')?.value || 'all';
  const now = Date.now() / 1000;

  const filtered = _rawKeys.filter(k => {
    const matchesSearch = k.key.toLowerCase().includes(query);
    const matchesTier = (tier === 'all') || (k.tier === tier);
    const fr = k.frozen_until > now;
    const stKey = !k.enabled ? 'disabled' : fr ? 'frozen' : k.consecutive_failures >= 3 ? 'degraded' : 'healthy';
    const matchesStatus = (status === 'all') || (stKey === status);
    return matchesSearch && matchesTier && matchesStatus;
  });

  $('ks-cnt').textContent = `${filtered.length} / ${_rawKeys.length} ${t('keys_count')}`;

  if (!filtered.length) {
    $('ks-tb').innerHTML = emptyRow(8, t('no_keys_found'));
    return;
  }

  const tiers = {
    'admin': { label: t('group_admin'), color: '#a5b4fc', bg: 'rgba(99, 102, 241, 0.08)', list: [] },
    'premium': { label: t('group_premium'), color: '#fbbf24', bg: 'rgba(245, 158, 11, 0.08)', list: [] },
    'free': { label: t('group_free'), color: '#d1d5db', bg: 'rgba(156, 163, 175, 0.08)', list: [] }
  };

  filtered.forEach(k => {
    const t = k.tier || 'free';
    if (tiers[t]) tiers[t].list.push(k);
    else tiers['free'].list.push(k);
  });

  let html = '';
  ['admin', 'premium', 'free'].forEach(tKey => {
    const group = tiers[tKey];
    if (group.list.length > 0) {
      html += `<tr><td colspan="8" style="background:${group.bg};font-weight:700;padding:12px 20px;color:${group.color};font-size:11px;letter-spacing:0.5px">${group.label} (${group.list.length})</td></tr>`;
      html += group.list.map(k => {
        const fr = k.frozen_until > now;
        const dot = !k.enabled ? 'hx' : fr ? 'ha' : k.consecutive_failures >= 3 ? 'hr' : 'hg';
        const st = !k.enabled ? t('st_disabled') : fr ? t('st_cooldown') : k.consecutive_failures >= 3 ? t('st_degraded') : t('st_healthy');
        const sb = !k.enabled ? 'bx' : fr ? 'ba' : k.consecutive_failures >= 3 ? 'br' : 'bg';
        return `
          <tr>
            <td><code style="font-weight:500;color:#c7d2fe">${k.key}</code></td>
            <td>${tBadge(k.tier)}</td>
            <td><span class="hd ${dot}"></span><span class="b ${sb}">${st}</span></td>
            <td>${fmt(k.today || 0)}</td>
            <td>${fmt(k.usage || 0)}</td>
            <td>${k.active_requests || 0}</td>
            <td>${k.consecutive_failures > 0 ? `<span style="color:var(--rose)">${k.consecutive_failures}</span>` : 0}</td>
            <td>${fr ? `<span style="color:var(--amber)">${relt(k.frozen_until)}</span>` : '—'}</td>
          </tr>
        `;
      }).join('');
    }
  });
  $('ks-tb').innerHTML = html;
}

function filterKeys() {
  renderFilteredKeys();
}

// [ADMIN] Accounts
async function loadAc(force = false) {
  if (!_rawAccounts || !_rawAccounts.length) {
    $('ac-tb').innerHTML = `<tr><td colspan="7">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !_rawAccounts || !_rawAccounts.length) {
      const data = await api('/dashboard/accounts');
      if (!data) return;
      _rawAccounts = data.accounts || [];
    }
    const bt = { free: 0, premium: 0, admin: 0 };
    _rawAccounts.forEach(a => { bt[a.tier || 'free'] = (bt[a.tier || 'free'] || 0) + 1; });

    if ($('ac-card-total')) {
      animateUpdateText('ac-card-total', _rawAccounts.length.toString());
      animateUpdateText('ac-card-free', (bt.free || 0).toString());
      animateUpdateText('ac-card-premium', (bt.premium || 0).toString());
      animateUpdateText('ac-card-admin', (bt.admin || 0).toString());
    } else {
      $('ac-cards').innerHTML = `
        <div class="sc">
          <div class="sc-lb">${t('ac_card_total')}</div>
          <div class="sc-v num-animate" id="ac-card-total">${_rawAccounts.length}</div>
          <div class="sc-s">${t('ac_card_total_sub')}</div>
        </div>
        <div class="sc cg">
          <div class="sc-lb">${t('ac_card_free')}</div>
          <div class="sc-v num-animate" id="ac-card-free">${bt.free || 0}</div>
          <div class="sc-s">${t('ac_card_free_sub')}</div>
        </div>
        <div class="sc ca">
          <div class="sc-lb">${t('ac_card_premium')}</div>
          <div class="sc-v num-animate" id="ac-card-premium">${bt.premium || 0}</div>
          <div class="sc-s">${t('ac_card_premium_sub')}</div>
        </div>
        <div class="sc cp">
          <div class="sc-lb">${t('ac_card_admin')}</div>
          <div class="sc-v num-animate" id="ac-card-admin">${bt.admin || 0}</div>
          <div class="sc-s">${t('ac_card_admin_sub')}</div>
        </div>
      `;
    }

    renderFilteredAccounts();
  } catch (e) {
    $('ac-tb').innerHTML = `<tr><td colspan="7" style="color:var(--rose);padding:16px">${e.message}</td></tr>`;
  }
}

function renderFilteredAccounts() {
  const query = ($('ac-search')?.value || '').toLowerCase().trim();
  const tier = $('ac-filter-tier')?.value || 'all';
  const status = $('ac-filter-status')?.value || 'all';

  const filtered = _rawAccounts.filter(a => {
    const matchesSearch = a.name.toLowerCase().includes(query) || 
                          (a.account_id && a.account_id.toLowerCase().includes(query));
    const matchesTier = (tier === 'all') || (a.tier === tier);
    const matchesStatus = (status === 'all') || 
                          (status === 'active' && a.enabled) || 
                          (status === 'disabled' && !a.enabled);
    return matchesSearch && matchesTier && matchesStatus;
  });

  $('ac-cnt').textContent = `${filtered.length} / ${_rawAccounts.length} ${t('accounts_count')}`;

  if (!filtered.length) {
    $('ac-tb').innerHTML = emptyRow(7, t('no_accounts_found'));
    return;
  }

  $('ac-tb').innerHTML = filtered.map(a => `
    <tr>
      <td><strong>${a.name}</strong></td>
      <td>${tBadge(a.tier)}</td>
      <td>${sBadge(a.enabled)}</td>
      <td>${(a.rpm || 0).toLocaleString()}</td>
      <td>${fmt(a.tpm || 0)}</td>
      <td>${(a.rpd || 0).toLocaleString()}</td>
      <td style="color:var(--text-muted)">${fmtD(a.created_at)}</td>
    </tr>
  `).join('');
}

function filterAccounts() {
  renderFilteredAccounts();
}

// [ADMIN] Usage Analytics
async function loadUs(force = false) {
  const el = $('us-ct');
  if (!_statsData) {
    el.innerHTML = spHtml();
  }
  try {
    if (force || !_statsData) {
      const data = await api('/api/stats?days=30');
      if (!data) return;
      _statsData = data;
    }
    const tk = _statsData.top_keys || [];
    if (!tk.length) {
      el.innerHTML = `<div style="text-align:center;padding:48px;color:var(--text-dark)">📭 ${t('no_data')}</div>`;
      return;
    }
    const mx = Math.max(...tk.map(x => x.t));
    el.innerHTML = tk.map((k, i) => `
      <div style="margin-bottom:20px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:13px;color:var(--text)">
            <strong>${k.account_name}</strong>
            <code style="font-size:11px;color:#a5b4fc;margin-left:8px;letter-spacing:0.5px">${k.full_key}</code>
          </span>
          <span style="font-size:12px;color:var(--text-muted)">${fmt(k.t)} tokens · ${k.req} ${t('requests_count')}</span>
        </div>
        <div class="pw">
          <div class="pb" style="width:${(k.t / mx * 100).toFixed(0)}%;background:${CLR[i % CLR.length]}"></div>
        </div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<p style="color:var(--rose)">${t('load_error')}: ${e.message}</p>`;
  }
}

// [ADMIN] Endpoints
async function loadEp(force = false) {
  if (!_rawEndpoints) {
    $('ep-tb').innerHTML = `<tr><td colspan="6">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !_rawEndpoints) {
      const data = await api('/dashboard/endpoints');
      if (!data) return;
      _rawEndpoints = data.endpoints || [];
    }
    const eps = _rawEndpoints;
    $('ep-cnt').textContent = `${eps.length} ${t('endpoints_count')}`;
    $('ep-tb').innerHTML = eps.map(e => `
      <tr>
        <td><strong>${e.name}</strong></td>
        <td style="font-family:monospace;color:var(--text-muted);font-size:12px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${e.base_url}">${e.base_url}</td>
        <td>${sBadge(e.enabled)}</td>
        <td><code style="color:var(--text-muted)">${(e.models || []).slice(0, 3).join(', ') || '—'}${(e.models || []).length > 3 ? '…' : ''}</code></td>
        <td>${e.fallback ? `<span class="b ba">${t('opt_active')}</span>` : `<span class="b bx">${t('opt_disabled')}</span>`}</td>
        <td style="color:var(--text-dark)">${e.updated_at || '—'}</td>
      </tr>
    `).join('') || emptyRow(6, t('no_endpoints'));
  } catch (e) {
    $('ep-tb').innerHTML = `<tr><td colspan="6" style="color:var(--rose);padding:16px">${e.message}</td></tr>`;
  }
}

// [ADMIN] Penalties
async function loadPe(force = false) {
  const el = $('pe-ct');
  if (!_rawPenalties) {
    el.innerHTML = spHtml();
  }
  try {
    if (force || !_rawPenalties) {
      const data = await api('/dashboard/penalties');
      if (!data) return;
      _rawPenalties = data.penalties || [];
    }
    const ps = _rawPenalties;
    const nb = $('nbp');
    nb.textContent = ps.length;
    nb.style.display = ps.length ? 'inline' : 'none';
    
    if (!ps.length) {
      el.innerHTML = `
        <div style="text-align:center;padding:64px;color:var(--text-muted)">
          <div class="sp" style="border-top-color:var(--emerald);animation:none;width:40px;height:40px;margin-bottom:16px;border-width:3px;display:flex;align-items:center;justify-content:center;color:var(--emerald)">✓</div>
          ${t('no_penalties')}
        </div>
      `;
      return;
    }
    
    el.innerHTML = `
      <div class="tcd">
        <div class="tscr">
          <table>
            <thead>
              <tr>
                <th class="tooltip" data-tip-i18n="tip_key_code" data-tip="" data-i18n="th_key_code">${t('th_key_code')}</th>
                <th data-i18n="th_model">${t('th_model')}</th>
                <th data-i18n="lbl_error_reason">${t('lbl_error_reason')}</th>
                <th data-i18n="th_score_reduction">${t('th_score_reduction')}</th>
                <th data-i18n="lbl_expires_after">${t('lbl_expires_after')}</th>
              </tr>
            </thead>
            <tbody>
              ${ps.map(p => `
                <tr>
                  <td><code style="font-weight:500;color:#c7d2fe">${p.key}</code></td>
                  <td><code>${p.model_id || t('lbl_global')}</code></td>
                  <td style="color:var(--amber)"><code>${p.reason || '—'}</code></td>
                  <td><span class="b br">-${p.score_reduction || 0}</span></td>
                  <td style="color:var(--text-muted)">${relt(p.expires)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
    
    document.querySelectorAll('#pe-ct [data-tip-i18n]').forEach(el => {
      const k = el.getAttribute('data-tip-i18n');
      el.setAttribute('data-tip', t(k));
    });
  } catch (e) {
    el.innerHTML = `<p style="color:var(--rose);padding:20px">${t('load_error')}: ${e.message}</p>`;
  }
}

// [ADMIN] Model Usage pricing analysis
async function loadMu(force = false) {
  try {
    if (force || !_statsData) {
      const data = await api('/api/stats?days=30');
      if (!data) return;
      _statsData = data;
    }
    const sav = _statsData.savings || { standard_cost: 0, cached_cost: 0, gemini_cost: 0, net_savings: 0 };
    $('std-cost-val').textContent = `$${(sav.standard_cost || 0).toFixed(4)}`;
    $('cache-cost-val').textContent = `$${(sav.cached_cost || 0).toFixed(4)}`;
    $('gemini-cost-val').textContent = `$${(sav.gemini_cost || 0).toFixed(4)}`;
    $('save-cost-val').textContent = `$${(sav.net_savings || 0).toFixed(4)}`;
    
    if (force || !_rawPools) {
      const poolData = await api('/api/model-pools-detail');
      if (poolData) {
        _rawPools = poolData.pools || [];
      }
    }
    if (_rawPools) {
      $('mu-tb').innerHTML = _rawPools.map(p => {
        let badgeClass = 'bg';
        if (p.name.includes('pro')) badgeClass = 'bp';
        return `
          <tr>
            <td><code>${p.name}</code></td>
            <td><code style="color:#c7d2fe;letter-spacing:0.5px">${p.models}</code></td>
            <td>${p.rpm}</td>
            <td>${p.tpm}</td>
            <td><span class="b ${badgeClass}">${p.status}</span></td>
          </tr>
        `;
      }).join('');
    }
  } catch (e) {}
}

// [USER] My Account
async function loadMyAcc(force = false) {
  const el = $('myacc-ct');
  if (!_rawMyAcc) {
    el.innerHTML = spHtml();
  }
  try {
    if (force || !_rawMyAcc) {
      const data = await api('/dashboard/me');
      if (!data) return;
      _rawMyAcc = data;
    }
    const data = _rawMyAcc;
    const tier = data.tier || 'free';
    
    const flash = data.flash || { rpm: 0, tpm: 0, rpd: 0, rpm_used: 0, tpm_used: 0, rpd_used: 0, rpm_left: 0, tpm_left: 0, rpd_left: 0 };
    const lite = data.lite || { rpm: 0, tpm: 0, rpd: 0, rpm_used: 0, tpm_used: 0, rpd_used: 0, rpm_left: 0, tpm_left: 0, rpd_left: 0 };
    
    // RPD reset countdown
    const now = new Date();
    const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    const diffMs = tomorrow - now;
    const hrs = Math.floor(diffMs / 3600000);
    const mins = Math.floor((diffMs % 3600000) / 60000);
    const resetCountdown = `${hrs}h ${mins}m`;

    const flashRpmPct = Math.min(100, Math.round((flash.rpm_used / (flash.rpm || 1)) * 100)) || 0;
    const flashTpmPct = Math.min(100, Math.round((flash.tpm_used / (flash.tpm || 1)) * 100)) || 0;
    const flashRpdPct = Math.min(100, Math.round((flash.rpd_used / (flash.rpd || 1)) * 100)) || 0;

    const liteRpmPct = Math.min(100, Math.round((lite.rpm_used / (lite.rpm || 1)) * 100)) || 0;
    const liteTpmPct = Math.min(100, Math.round((lite.tpm_used / (lite.tpm || 1)) * 100)) || 0;
    const liteRpdPct = Math.min(100, Math.round((lite.rpd_used / (lite.rpd || 1)) * 100)) || 0;
    
    const getBarColor = (pct) => {
      if (pct < 50) return 'var(--emerald)';
      if (pct < 80) return 'var(--amber)';
      return 'var(--rose)';
    };
    
    const flashRpmColor = getBarColor(flashRpmPct);
    const flashTpmColor = getBarColor(flashTpmPct);
    const flashRpdColor = getBarColor(flashRpdPct);

    const liteRpmColor = getBarColor(liteRpmPct);
    const liteTpmColor = getBarColor(liteTpmPct);
    const liteRpdColor = getBarColor(liteRpdPct);

    if ($('myacc-flash-rpm-val')) {
      animateUpdateText('myacc-name', data.name);
      const tierBadgeEl = $('myacc-tier');
      if (tierBadgeEl) tierBadgeEl.innerHTML = tBadge(tier);
      animateUpdateText('myacc-id', data.account_id || '—');
      
      // Flash Pool
      animateUpdateText('myacc-flash-rpm-val', `${flash.rpm_used} / ${(flash.rpm||0).toLocaleString()}`);
      updateProgressBar('myacc-flash-rpm-bar', flashRpmPct, flashRpmColor);
      animateUpdateText('myacc-flash-rpm-left', flash.rpm_left.toLocaleString());
      animateUpdateText('myacc-flash-rpm-pct', `${flashRpmPct}%`);
      
      animateUpdateText('myacc-flash-tpm-val', `${fmt(flash.tpm_used)} / ${fmt(flash.tpm||0)}`);
      updateProgressBar('myacc-flash-tpm-bar', flashTpmPct, flashTpmColor);
      animateUpdateText('myacc-flash-tpm-left', fmt(flash.tpm_left));
      animateUpdateText('myacc-flash-tpm-pct', `${flashTpmPct}%`);
      
      animateUpdateText('myacc-flash-rpd-val', `${flash.rpd_used} / ${fmt(flash.rpd||0)}`);
      updateProgressBar('myacc-flash-rpd-bar', flashRpdPct, flashRpdColor);
      animateUpdateText('myacc-flash-rpd-left', flash.rpd_left.toLocaleString());
      
      // Lite Pool
      animateUpdateText('myacc-lite-rpm-val', `${lite.rpm_used} / ${(lite.rpm||0).toLocaleString()}`);
      updateProgressBar('myacc-lite-rpm-bar', liteRpmPct, liteRpmColor);
      animateUpdateText('myacc-lite-rpm-left', lite.rpm_left.toLocaleString());
      animateUpdateText('myacc-lite-rpm-pct', `${liteRpmPct}%`);
      
      animateUpdateText('myacc-lite-tpm-val', `${fmt(lite.tpm_used)} / ${fmt(lite.tpm||0)}`);
      updateProgressBar('myacc-lite-tpm-bar', liteTpmPct, liteTpmColor);
      animateUpdateText('myacc-lite-tpm-left', fmt(lite.tpm_left));
      animateUpdateText('myacc-lite-tpm-pct', `${liteTpmPct}%`);
      
      animateUpdateText('myacc-lite-rpd-val', `${lite.rpd_used} / ${fmt(lite.rpd||0)}`);
      updateProgressBar('myacc-lite-rpd-bar', liteRpdPct, liteRpdColor);
      animateUpdateText('myacc-lite-rpd-left', lite.rpd_left.toLocaleString());
      
      // Update tomorrow timestamp attribute for reset countdown
      document.querySelectorAll('#myacc-ct .rpd-reset-countdown').forEach(el => {
        el.setAttribute('data-tomorrow-ts', Math.floor(tomorrow.getTime() / 1000));
        el.textContent = resetCountdown;
      });
      
      // Flash Capacity Pool Summary
      animateUpdateText('myacc-flash-rpd', `${fmt(data.flash_pool.rpd_left)} / ${fmt(data.flash_pool.rpd_limit)}`);
      animateUpdateText('myacc-flash-1h', `${fmt(data.flash_pool.tokens_1h_left)} / ${fmt(data.flash_pool.tokens_1h_limit)}`);
      animateUpdateText('myacc-flash-12h', `${fmt(data.flash_pool.tokens_12h_left)} / ${fmt(data.flash_pool.tokens_12h_limit)}`);
      animateUpdateText('myacc-flash-24h', `${fmt(data.flash_pool.tokens_24h_left)} / ${fmt(data.flash_pool.tokens_24h_limit)}`);
      
      // Lite Capacity Pool Summary
      animateUpdateText('myacc-lite-rpd', `${fmt(data.lite_pool.rpd_left)} / ${fmt(data.lite_pool.rpd_limit)}`);
      animateUpdateText('myacc-lite-1h', `${fmt(data.lite_pool.tokens_1h_left)} / ${fmt(data.lite_pool.tokens_1h_limit)}`);
      animateUpdateText('myacc-lite-12h', `${fmt(data.lite_pool.tokens_12h_left)} / ${fmt(data.lite_pool.tokens_12h_limit)}`);
      animateUpdateText('myacc-lite-24h', `${fmt(data.lite_pool.tokens_24h_left)} / ${fmt(data.lite_pool.tokens_24h_limit)}`);
      
      const avatarEl = $('myacc-avatar');
      if (avatarEl) {
        avatarEl.textContent = data.name.substring(0,2).toUpperCase();
      }
    } else {
      el.innerHTML = `
        <div style="max-width:100%">
          <!-- User profile header card -->
          <div style="background:linear-gradient(135deg, rgba(99,102,241,0.08), rgba(6,182,212,0.02));border:1px solid rgba(99,102,241,0.12);border-radius:var(--radius);padding:24px;margin-bottom:28px;display:flex;align-items:center;gap:20px;box-shadow:0 8px 32px rgba(99,102,241,0.04)">
            <div id="myacc-avatar" style="width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg, var(--primary), var(--cyan));display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;color:white;box-shadow:0 0 16px var(--primary-glow)">
              ${data.name.substring(0,2).toUpperCase()}
            </div>
            <div style="flex:1">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
                <h2 id="myacc-name" style="font-size:18px;font-weight:800;letter-spacing:-0.3px;color:var(--text);margin:0">${data.name}</h2>
                <span id="myacc-tier">${tBadge(tier)}</span>
              </div>
              <div style="font-size:11px;color:var(--text-muted);font-family:monospace;display:flex;align-items:center;gap:6px">
                <span>${t('lbl_account_id')}</span>
                <span id="myacc-id" style="color:#c7d2fe">${data.account_id || '—'}</span>
              </div>
            </div>
          </div>

          <!-- Flash Pool Limits Section -->
          <div style="margin-bottom:16px">
            <h3 style="font-size:14px;font-weight:700;color:var(--text);margin-bottom:4px">Gemini Flash Pool — ${t('lbl_usage_limits')}</h3>
            <p style="font-size:12px;color:var(--text-muted)">${t('lbl_usage_limits_sub')}</p>
          </div>
          <div class="cards" style="grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px">
            <!-- RPM Card -->
            <div class="dlim">
              <div class="dlim-h">
                <span class="dlim-t tooltip" data-tip-i18n="tip_rpm" data-tip="">${t('lbl_rpm_card')}</span>
                <span style="color:var(--cyan);display:flex">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                  </svg>
                </span>
              </div>
              <div class="dlim-v num-animate" id="myacc-flash-rpm-val">${flash.rpm_used} / ${(flash.rpm||0).toLocaleString()}</div>
              <div class="dlim-progress">
                <div class="dlim-bar" id="myacc-flash-rpm-bar" style="width: ${flashRpmPct}%; background: ${flashRpmColor}"></div>
              </div>
              <div class="dlim-info">
                <div class="dlim-info-item">${t('lbl_left')} <span id="myacc-flash-rpm-left" style="color:var(--cyan)">${flash.rpm_left.toLocaleString()}</span></div>
                <div class="dlim-info-item">${t('lbl_using')} <span id="myacc-flash-rpm-pct">${flashRpmPct}%</span></div>
              </div>
            </div>

            <!-- TPM Card -->
            <div class="dlim">
              <div class="dlim-h">
                <span class="dlim-t tooltip" data-tip-i18n="tip_tpm" data-tip="">${t('lbl_tpm_card')}</span>
                <span style="color:var(--emerald);display:flex">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                    <polyline points="4 7 4 4 20 4 20 7"></polyline>
                    <line x1="9" y1="20" x2="15" y2="20"></line>
                    <line x1="12" y1="4" x2="12" y2="20"></line>
                  </svg>
                </span>
              </div>
              <div class="dlim-v num-animate" id="myacc-flash-tpm-val">${fmt(flash.tpm_used)} / ${fmt(flash.tpm||0)}</div>
              <div class="dlim-progress">
                <div class="dlim-bar" id="myacc-flash-tpm-bar" style="width: ${flashTpmPct}%; background: ${flashTpmColor}"></div>
              </div>
              <div class="dlim-info">
                <div class="dlim-info-item">${t('lbl_left')} <span id="myacc-flash-tpm-left" style="color:var(--emerald)">${fmt(flash.tpm_left)}</span></div>
                <div class="dlim-info-item">${t('lbl_using')} <span id="myacc-flash-tpm-pct">${flashTpmPct}%</span></div>
              </div>
            </div>

            <!-- RPD Card -->
            <div class="dlim">
              <div class="dlim-h">
                <span class="dlim-t tooltip" data-tip-i18n="tip_rpd" data-tip="">${t('lbl_rpd_card')}</span>
                <span style="color:var(--amber);display:flex">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                  </svg>
                </span>
              </div>
              <div class="dlim-v num-animate" id="myacc-flash-rpd-val">${flash.rpd_used} / ${fmt(flash.rpd||0)}</div>
              <div class="dlim-progress">
                <div class="dlim-bar" id="myacc-flash-rpd-bar" style="width: ${flashRpdPct}%; background: ${flashRpdColor}"></div>
              </div>
              <div class="dlim-info">
                <div class="dlim-info-item">${t('lbl_left')} <span id="myacc-flash-rpd-left" style="color:var(--amber)">${flash.rpd_left.toLocaleString()}</span></div>
                <div class="dlim-info-item">${t('lbl_reset')} <span class="rpd-reset-countdown" data-tomorrow-ts="${Math.floor(tomorrow.getTime()/1000)}">${resetCountdown}</span></div>
              </div>
            </div>
          </div>

          <!-- Lite Pool Limits Section -->
          <div style="margin-bottom:16px;margin-top:24px">
            <h3 style="font-size:14px;font-weight:700;color:var(--text);margin-bottom:4px">Gemini Flash Lite Pool — ${t('lbl_usage_limits')}</h3>
            <p style="font-size:12px;color:var(--text-muted)">${t('lbl_usage_limits_sub')}</p>
          </div>
          <div class="cards" style="grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px">
            <!-- RPM Card -->
            <div class="dlim">
              <div class="dlim-h">
                <span class="dlim-t tooltip" data-tip-i18n="tip_rpm" data-tip="">${t('lbl_rpm_card')}</span>
                <span style="color:var(--cyan);display:flex">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                  </svg>
                </span>
              </div>
              <div class="dlim-v num-animate" id="myacc-lite-rpm-val">${lite.rpm_used} / ${(lite.rpm||0).toLocaleString()}</div>
              <div class="dlim-progress">
                <div class="dlim-bar" id="myacc-lite-rpm-bar" style="width: ${liteRpmPct}%; background: ${liteRpmColor}"></div>
              </div>
              <div class="dlim-info">
                <div class="dlim-info-item">${t('lbl_left')} <span id="myacc-lite-rpm-left" style="color:var(--cyan)">${lite.rpm_left.toLocaleString()}</span></div>
                <div class="dlim-info-item">${t('lbl_using')} <span id="myacc-lite-rpm-pct">${liteRpmPct}%</span></div>
              </div>
            </div>

            <!-- TPM Card -->
            <div class="dlim">
              <div class="dlim-h">
                <span class="dlim-t tooltip" data-tip-i18n="tip_tpm" data-tip="">${t('lbl_tpm_card')}</span>
                <span style="color:var(--emerald);display:flex">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                    <polyline points="4 7 4 4 20 4 20 7"></polyline>
                    <line x1="9" y1="20" x2="15" y2="20"></line>
                    <line x1="12" y1="4" x2="12" y2="20"></line>
                  </svg>
                </span>
              </div>
              <div class="dlim-v num-animate" id="myacc-lite-tpm-val">${fmt(lite.tpm_used)} / ${fmt(lite.tpm||0)}</div>
              <div class="dlim-progress">
                <div class="dlim-bar" id="myacc-lite-tpm-bar" style="width: ${liteTpmPct}%; background: ${liteTpmColor}"></div>
              </div>
              <div class="dlim-info">
                <div class="dlim-info-item">${t('lbl_left')} <span id="myacc-lite-tpm-left" style="color:var(--emerald)">${fmt(lite.tpm_left)}</span></div>
                <div class="dlim-info-item">${t('lbl_using')} <span id="myacc-lite-tpm-pct">${liteTpmPct}%</span></div>
              </div>
            </div>

            <!-- RPD Card -->
            <div class="dlim">
              <div class="dlim-h">
                <span class="dlim-t tooltip" data-tip-i18n="tip_rpd" data-tip="">${t('lbl_rpd_card')}</span>
                <span style="color:var(--amber);display:flex">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                  </svg>
                </span>
              </div>
              <div class="dlim-v num-animate" id="myacc-lite-rpd-val">${lite.rpd_used} / ${fmt(lite.rpd||0)}</div>
              <div class="dlim-progress">
                <div class="dlim-bar" id="myacc-lite-rpd-bar" style="width: ${liteRpdPct}%; background: ${liteRpdColor}"></div>
              </div>
              <div class="dlim-info">
                <div class="dlim-info-item">${t('lbl_left')} <span id="myacc-lite-rpd-left" style="color:var(--amber)">${lite.rpd_left.toLocaleString()}</span></div>
                <div class="dlim-info-item">${t('lbl_reset')} <span class="rpd-reset-countdown" data-tomorrow-ts="${Math.floor(tomorrow.getTime()/1000)}">${resetCountdown}</span></div>
              </div>
            </div>
          </div>
          
          <!-- Virtual Pools Section -->
          <div style="margin-top:32px">
            <div style="margin-bottom:16px">
              <h3 style="font-size:14px;font-weight:700;color:var(--text);margin-bottom:4px">${t('lbl_key_pools')}</h3>
              <p style="font-size:12px;color:var(--text-muted)">${t('lbl_key_pools_sub')}</p>
            </div>
            
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
              <!-- Flash Pool -->
              <div style="background:linear-gradient(135deg, rgba(6,182,212,0.03), rgba(255,255,255,0.005));border:1px solid rgba(6,182,212,0.15);border-radius:var(--radius);padding:22px;box-shadow:0 8px 32px rgba(0,0,0,0.15)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;border-bottom:1px solid rgba(255,255,255,0.04);padding-bottom:12px">
                  <div style="display:flex;align-items:center;gap:10px">
                    <span style="color:var(--cyan);display:flex">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="2" y1="12" x2="22" y2="12"></line>
                        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                      </svg>
                    </span>
                    <span style="font-weight:700;color:var(--cyan);font-size:14px">Gemini Flash Pool</span>
                  </div>
                  <span class="b bc"><span class="hd hg"></span>${t('opt_active')}</span>
                </div>
                <div class="drow"><div class="drow-l">${t('lbl_pool_rpd')}</div><div class="drow-v num-animate" id="myacc-flash-rpd" style="color:var(--cyan)">${fmt(data.flash_pool.rpd_left)} / ${fmt(data.flash_pool.rpd_limit)}</div></div>
                <div class="drow"><div class="drow-l tooltip" data-tip-i18n="tip_total_tokens" data-tip="">${t('lbl_pool_1h')}</div><div class="drow-v num-animate" id="myacc-flash-1h" style="color:var(--emerald);font-weight:700">${fmt(data.flash_pool.tokens_1h_left)} / ${fmt(data.flash_pool.tokens_1h_limit)}</div></div>
                <div class="drow"><div class="drow-l tooltip" data-tip-i18n="tip_total_tokens" data-tip="">${t('lbl_pool_12h')}</div><div class="drow-v num-animate" id="myacc-flash-12h" style="color:var(--amber);font-weight:700">${fmt(data.flash_pool.tokens_12h_left)} / ${fmt(data.flash_pool.tokens_12h_limit)}</div></div>
                <div class="drow"><div class="drow-l tooltip" data-tip-i18n="tip_total_tokens" data-tip="">${t('lbl_pool_24h')}</div><div class="drow-v num-animate" id="myacc-flash-24h" style="color:var(--primary);font-weight:700">${fmt(data.flash_pool.tokens_24h_left)} / ${fmt(data.flash_pool.tokens_24h_limit)}</div></div>
              </div>
              
              <!-- Flash Lite Pool -->
              <div style="background:linear-gradient(135deg, rgba(16,185,129,0.03), rgba(255,255,255,0.005));border:1px solid rgba(16,185,129,0.15);border-radius:var(--radius);padding:22px;box-shadow:0 8px 32px rgba(0,0,0,0.15)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;border-bottom:1px solid rgba(255,255,255,0.04);padding-bottom:12px">
                  <div style="display:flex;align-items:center;gap:10px">
                    <span style="color:var(--emerald);display:flex">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                      </svg>
                    </span>
                    <span style="font-weight:700;color:var(--emerald);font-size:14px">Gemini Flash Lite Pool</span>
                  </div>
                  <span class="b bg"><span class="hd hg"></span>${t('opt_active')}</span>
                </div>
                <div class="drow"><div class="drow-l">${t('lbl_pool_rpd')}</div><div class="drow-v num-animate" id="myacc-lite-rpd" style="color:var(--emerald)">${fmt(data.lite_pool.rpd_left)} / ${fmt(data.lite_pool.rpd_limit)}</div></div>
                <div class="drow"><div class="drow-l tooltip" data-tip-i18n="tip_total_tokens" data-tip="">${t('lbl_pool_1h')}</div><div class="drow-v num-animate" id="myacc-lite-1h" style="color:var(--emerald);font-weight:700">${fmt(data.lite_pool.tokens_1h_left)} / ${fmt(data.lite_pool.tokens_1h_limit)}</div></div>
                <div class="drow"><div class="drow-l tooltip" data-tip-i18n="tip_total_tokens" data-tip="">${t('lbl_pool_12h')}</div><div class="drow-v num-animate" id="myacc-lite-12h" style="color:var(--amber);font-weight:700">${fmt(data.lite_pool.tokens_12h_left)} / ${fmt(data.lite_pool.tokens_12h_limit)}</div></div>
                <div class="drow"><div class="drow-l tooltip" data-tip-i18n="tip_total_tokens" data-tip="">${t('lbl_pool_24h')}</div><div class="drow-v num-animate" id="myacc-lite-24h" style="color:var(--primary);font-weight:700">${fmt(data.lite_pool.tokens_24h_left)} / ${fmt(data.lite_pool.tokens_24h_limit)}</div></div>
              </div>
            </div>
          </div>
        </div>
      `;
    }
    
    document.querySelectorAll('#myacc-ct [data-tip-i18n]').forEach(el => {
      const k = el.getAttribute('data-tip-i18n');
      el.setAttribute('data-tip', t(k));
    });
  } catch (e) {
    el.innerHTML = `<p style="color:var(--rose);padding:20px">${t('load_error')}: ${e.message}</p>`;
  }
}

// [USER] My Usage
async function loadMyUse(force = false) {
  if (!_myStatsData) {
    $('myuse-cards').innerHTML = '';
    $('myuse-tb').innerHTML = `<tr><td colspan="5">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !_myStatsData) {
      const data = await api('/dashboard/my-stats?days=30');
      if (!data) return;
      _myStatsData = data;
    }
    const s = _myStatsData.summary || [];
    const d = _myStatsData.daily || [];
    
    const tot = s.reduce((a, b) => a + (b.t || 0), 0);
    const req = s.reduce((a, b) => a + (b.req || 0), 0);
    const now = new Date();
    const td = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const tdd = d.filter(x => x.d === td);
    const tdt = tdd.reduce((a, b) => a + (b.t || 0), 0);
    const tdr = tdd.reduce((a, b) => a + (b.req || 0), 0);
    const sav = _myStatsData.savings ? (_myStatsData.savings.net_savings || 0) : 0;

    if ($('my-card-today-tokens')) {
      animateUpdateText('my-card-today-tokens', fmt(tdt));
      animateUpdateText('my-card-today-reqs', `${tdr.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('my-card-30d-tokens', fmt(tot));
      animateUpdateText('my-card-30d-reqs', `${req.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('my-card-active-models', s.length.toString());
      setTimeout(() => animateSavings(sav, 'my'), 100);
    } else {
      $('myuse-cards').innerHTML = statsCards(s, d, _myStatsData.savings, 'my');
    }
    
    $('myuse-tb').innerHTML = statsTable(s);
    
    const ld = buildDS(d);
    if (_ch.myday && ld.labels.length) {
      updateLineChart(_ch.myday, ld.labels, ld.datasets);
    } else if (ld.labels.length) {
      if (_ch.myday) _ch.myday.destroy();
      _ch.myday = mkLine($('cMyDay').getContext('2d'), ld.labels, ld.datasets);
    }
    
    if (_ch.mymod && s.length) {
      updateDonutChart(_ch.mymod, s.map(x => x.model_alias), s.map(x => x.t || 0));
    } else if (s.length) {
      if (_ch.mymod) _ch.mymod.destroy();
      _ch.mymod = mkDonut($('cMyMod').getContext('2d'), s.map(x => x.model_alias), s.map(x => x.t || 0));
    }
  } catch (e) {
    $('myuse-tb').innerHTML = `<tr><td colspan="5" style="color:var(--rose);padding:16px">${t('load_error')}: ${e.message}</td></tr>`;
  }
}

// Auto-login check & Initial Boot
applyLanguage(_lang);
applyTheme(_theme);

if (_tok) {
  enterApp();
}
