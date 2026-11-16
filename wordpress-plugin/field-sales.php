<?php
/**
 * Plugin Name: Agamsale
 * Description: Run the Agamsale app (visits, orders, customers) inside WooCommerce, and take Razorpay payments at checkout through Agamsale. Connect it once on Settings → Agamsale.
 * Version: 1.3.0
 * Author: Agamsale
 * Requires at least: 6.0
 * Requires PHP: 7.4
 * Requires Plugins: woocommerce
 * WC requires at least: 8.0
 * WC tested up to: 9.4
 */

if (!defined('ABSPATH')) {
	exit;
}

const FIELD_SALES_OPTION = 'field_sales_app_url';
const FIELD_SALES_SECRET_OPTION = 'field_sales_shared_secret';
const FIELD_SALES_CAP = 'manage_woocommerce';
// Must match AUDIENCE in field_sales_woocommerce/wordpress_embed.py.
const FIELD_SALES_TOKEN_AUDIENCE = 'agamsale-wordpress';
const FIELD_SALES_TOKEN_TTL = 300;
const FIELD_SALES_VERSION = '1.3.0';
const FIELD_SALES_PLUGIN_FILE = __FILE__;
// Optional: where WordPress reaches Agamsale's server, when the App URL's address is not
// reachable from this server (e.g. Docker), and the Frappe site name to send with it.
const FIELD_SALES_FRAPPE_URL_OPTION = 'field_sales_frappe_url';
const FIELD_SALES_FRAPPE_SITE_OPTION = 'field_sales_frappe_site';

require_once __DIR__ . '/includes/class-agamsale-api.php';

// Order storage (HPOS) and the Cart/Checkout blocks: the gateway uses WC_Order methods only.
add_action('before_woocommerce_init', function () {
	if (class_exists(\Automattic\WooCommerce\Utilities\FeaturesUtil::class)) {
		\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility('custom_order_tables', __FILE__, true);
		\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility('cart_checkout_blocks', __FILE__, true);
	}
});

// The Razorpay payment gateway (WooCommerce → Settings → Payments → Razorpay (Agamsale)).
add_action('plugins_loaded', function () {
	if (!class_exists('WC_Payment_Gateway')) {
		return;
	}
	require_once __DIR__ . '/includes/class-wc-gateway-agamsale-razorpay.php';
	add_filter('woocommerce_payment_gateways', function ($gateways) {
		$gateways[] = 'WC_Gateway_Agamsale_Razorpay';
		return $gateways;
	});
});

add_action('woocommerce_blocks_payment_method_type_registration', function ($registry) {
	if (!class_exists('\\Automattic\\WooCommerce\\Blocks\\Payments\\Integrations\\AbstractPaymentMethodType') || !class_exists('WC_Gateway_Agamsale_Razorpay')) {
		return;
	}
	require_once __DIR__ . '/includes/class-agamsale-razorpay-blocks.php';
	$registry->register(new Agamsale_Razorpay_Blocks());
});

function field_sales_url(): string {
	$url = trim((string) get_option(FIELD_SALES_OPTION, ''));
	return $url ? rtrim($url, '/') : '';
}

function field_sales_configured(): bool {
	return (bool) field_sales_url();
}

/** Who sees the Agamsale menu and may get a sign-in token (filter: agamsale_capability). */
function field_sales_cap(): string {
	return (string) apply_filters('agamsale_capability', FIELD_SALES_CAP);
}

/** scheme://host[:port] of a URL: the app frame's origin, for postMessage. */
function field_sales_origin(string $url): string {
	$parts = wp_parse_url($url);
	if (empty($parts['scheme']) || empty($parts['host'])) {
		return '';
	}
	$origin = strtolower($parts['scheme']) . '://' . strtolower($parts['host']);
	return isset($parts['port']) ? $origin . ':' . $parts['port'] : $origin;
}

/**
 * The secret shared with Agamsale (Store Connection → Plugin Secret). Made on first use;
 * kept out of autoload so it is not read on every request.
 */
function field_sales_secret(): string {
	$secret = (string) get_option(FIELD_SALES_SECRET_OPTION, '');
	if (strlen($secret) < 32) {
		$secret = wp_generate_password(48, false, false);
		update_option(FIELD_SALES_SECRET_OPTION, $secret, false);
	}
	return $secret;
}

function field_sales_b64url(string $data): string {
	return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

/** A 5-minute HS256 token naming the WordPress user; Agamsale signs in its user with this email. */
function field_sales_token(WP_User $user): string {
	$now = time();
	$claims = [
		'iss' => home_url('/'),
		'aud' => FIELD_SALES_TOKEN_AUDIENCE,
		'sub' => (string) $user->ID,
		'email' => $user->user_email,
		'name' => $user->display_name,
		'iat' => $now,
		'exp' => $now + FIELD_SALES_TOKEN_TTL,
	];
	$header = field_sales_b64url(wp_json_encode(['alg' => 'HS256', 'typ' => 'JWT']));
	$payload = field_sales_b64url(wp_json_encode($claims));
	$signature = field_sales_b64url(hash_hmac('sha256', $header . '.' . $payload, field_sales_secret(), true));
	return $header . '.' . $payload . '.' . $signature;
}

/**
 * Registrable domain, roughly: the last two labels, or three for a two-letter country
 * suffix after a short label (shop.co.uk, shop.com.au). Only drives a hint on the settings page.
 */
function field_sales_site_of(string $host): string {
	$labels = explode('.', strtolower($host));
	$n = count($labels);
	if ($n >= 3 && strlen($labels[$n - 1]) === 2 && strlen($labels[$n - 2]) <= 3) {
		return implode('.', array_slice($labels, -3));
	}
	return implode('.', array_slice($labels, -2));
}

/**
 * The Agamsale submenus in wp-admin, as WooCommerce lays out its own: each opens one app page
 * (a path under the App URL) in the frame. The app hides its own menu inside wp-admin, so these
 * are its navigation there; pages the person's Agamsale role cannot open send them back to the
 * Dashboard with a message.
 */
function field_sales_pages(): array {
	return [
		'field-sales' => ['Dashboard', 'dashboard'],
		'field-sales-orders' => ['Orders', 'orders'],
		'field-sales-shops' => ['Shops', 'retailers'],
		'field-sales-visits' => ['Visits', 'visits'],
		'field-sales-products' => ['Products', 'products'],
		'field-sales-reps' => ['Sales Reps', 'sales-reps'],
		'field-sales-commission' => ['Commission', 'commission'],
		'field-sales-reports' => ['Reports', 'reports'],
		'field-sales-status' => ['Status', 'sync-log'],
		'field-sales-app-settings' => ['Settings', 'settings'],
	];
}

/** An app path from wp-admin's URL (`&path=orders/FO-0001?tab=x`): relative, no scheme or host. */
function field_sales_clean_path(string $path): string {
	$path = ltrim($path, '/');
	if ($path === '' || strlen($path) > 300 || strpos($path, '..') !== false || strpos($path, '//') !== false) {
		return '';
	}
	return preg_match('#^[A-Za-z0-9][A-Za-z0-9/_.%~=&+,?-]*$#', $path) ? $path : '';
}

add_action('admin_menu', function () {
	add_menu_page(
		'Agamsale',
		'Agamsale',
		field_sales_cap(),
		'field-sales',
		'field_sales_render_page',
		'dashicons-store',
		56
	);
	foreach (field_sales_pages() as $slug => $page) {
		add_submenu_page('field-sales', $page[0] . ' ‹ Agamsale', $page[0], field_sales_cap(), $slug, 'field_sales_render_page');
	}
	add_options_page(
		'Agamsale',
		'Agamsale',
		'manage_options',
		'field-sales-settings',
		'field_sales_render_settings'
	);
});

add_action('admin_init', function () {
	register_setting('field_sales', FIELD_SALES_OPTION, [
		'type' => 'string',
		'sanitize_callback' => 'field_sales_sanitize_url',
	]);
	register_setting('field_sales', FIELD_SALES_FRAPPE_URL_OPTION, [
		'type' => 'string',
		'sanitize_callback' => 'field_sales_sanitize_url',
	]);
	register_setting('field_sales', FIELD_SALES_FRAPPE_SITE_OPTION, [
		'type' => 'string',
		'sanitize_callback' => function ($site) {
			$site = strtolower(trim((string) $site));
			return preg_match('/^[a-z0-9.-]{0,253}$/', $site) ? $site : '';
		},
	]);
});

function field_sales_sanitize_url($url) {
	$url = trim((string) $url);
	if ($url === '') {
		return '';
	}
	if (!preg_match('#^https?://#i', $url)) {
		$url = 'https://' . $url;
	}
	return esc_url_raw($url);
}

// The app frame asks for a sign-in token (see field_sales_render_page); logged-in users only.
add_action('wp_ajax_agamsale_token', function () {
	check_ajax_referer('agamsale_token');
	if (!current_user_can(field_sales_cap())) {
		wp_send_json_error(['message' => 'Your WordPress role cannot open Agamsale.'], 403);
	}
	wp_send_json_success([
		'token' => field_sales_token(wp_get_current_user()),
		'expires_in' => FIELD_SALES_TOKEN_TTL,
	]);
});

// Settings → Agamsale → Regenerate secret: tokens signed with the old one stop working.
add_action('admin_post_field_sales_regenerate_secret', function () {
	if (!current_user_can('manage_options')) {
		wp_die('Not allowed', 403);
	}
	check_admin_referer('field_sales_regenerate_secret');
	delete_option(FIELD_SALES_SECRET_OPTION);
	field_sales_secret();
	wp_safe_redirect(admin_url('options-general.php?page=field-sales-settings&secret-regenerated=1'));
	exit;
});

// Settings → Agamsale → Test connection / Test Razorpay: the answer is shown once on the page.
foreach (['field_sales_test_connection' => 'ping', 'field_sales_test_razorpay' => 'test_razorpay'] as $action => $endpoint) {
	add_action('admin_post_' . $action, function () use ($action, $endpoint) {
		if (!current_user_can('manage_options')) {
			wp_die('Not allowed', 403);
		}
		check_admin_referer($action);
		$answer = Agamsale_Api::call($endpoint);
		set_transient('field_sales_test_' . get_current_user_id(), [
			'what' => $endpoint === 'ping' ? 'Agamsale connection' : 'Razorpay',
			'ok' => !is_wp_error($answer),
			'message' => is_wp_error($answer) ? $answer->get_error_message() : ($answer['message'] ?? 'Connected.'),
			'status' => (!is_wp_error($answer) && isset($answer['razorpay'])) ? $answer['razorpay'] : null,
		], 300);
		wp_safe_redirect(admin_url('options-general.php?page=field-sales-settings#agamsale-payments'));
		exit;
	});
}

function field_sales_render_settings() {
	$url = field_sales_url();
	$secret = field_sales_secret();
	?>
	<div class="wrap">
		<h1>Agamsale</h1>
		<?php if (!empty($_GET['secret-regenerated'])) : ?>
			<div class="notice notice-warning"><p>New secret made. Paste it in Agamsale's Store Connection, or sign-in inside wp-admin stops working.</p></div>
		<?php endif; ?>
		<p>Embed the Agamsale app in your WooCommerce admin.</p>
		<form method="post" action="options.php">
			<?php settings_fields('field_sales'); ?>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="<?php echo esc_attr(FIELD_SALES_OPTION); ?>">App URL</label></th>
					<td>
						<input type="url" class="regular-text code" id="<?php echo esc_attr(FIELD_SALES_OPTION); ?>"
							name="<?php echo esc_attr(FIELD_SALES_OPTION); ?>"
							value="<?php echo esc_attr($url); ?>"
							placeholder="https://sales.example.com/fieldsales" />
						<p class="description">
							Where your Agamsale app is served, including <code>/fieldsales</code>.
						</p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="<?php echo esc_attr(FIELD_SALES_FRAPPE_URL_OPTION); ?>">Server URL</label></th>
					<td>
						<input type="url" class="regular-text code" id="<?php echo esc_attr(FIELD_SALES_FRAPPE_URL_OPTION); ?>"
							name="<?php echo esc_attr(FIELD_SALES_FRAPPE_URL_OPTION); ?>"
							value="<?php echo esc_attr((string) get_option(FIELD_SALES_FRAPPE_URL_OPTION, '')); ?>"
							placeholder="<?php echo esc_attr(field_sales_origin($url) ?: 'https://sales.example.com'); ?>" />
						<p class="description">
							Optional. Where this server reaches Agamsale for payments. Leave empty to use the App URL's address;
							set it when that address does not work from here (e.g. <code>http://host.docker.internal:8000</code>).
						</p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="<?php echo esc_attr(FIELD_SALES_FRAPPE_SITE_OPTION); ?>">Site name</label></th>
					<td>
						<input type="text" class="regular-text code" id="<?php echo esc_attr(FIELD_SALES_FRAPPE_SITE_OPTION); ?>"
							name="<?php echo esc_attr(FIELD_SALES_FRAPPE_SITE_OPTION); ?>"
							value="<?php echo esc_attr((string) get_option(FIELD_SALES_FRAPPE_SITE_OPTION, '')); ?>" />
						<p class="description">Optional. The Frappe site name, only when the Server URL does not name it.</p>
					</td>
				</tr>
			</table>
			<?php submit_button(); ?>
		</form>

		<h2>Sign in with WordPress</h2>
		<p>Inside wp-admin, Agamsale signs you in as the Agamsale user with <strong>the same email</strong> as your
			WordPress account (that user needs a Field Sales role). Paste this secret in the Agamsale app under
			<strong>Settings → Store Connection → WordPress Admin → Plugin Secret</strong>:</p>
		<p style="display:flex;gap:8px;align-items:center;max-width:640px;">
			<input type="text" readonly class="large-text code" id="field-sales-secret" value="<?php echo esc_attr($secret); ?>"
				onfocus="this.select();" />
			<button type="button" class="button" onclick="(function(b){var i=document.getElementById('field-sales-secret');i.select();(navigator.clipboard?navigator.clipboard.writeText(i.value):Promise.reject()).catch(function(){document.execCommand('copy');}).then(function(){b.textContent='Copied';});})(this)">Copy</button>
		</p>
		<form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>"
			onsubmit="return confirm('Make a new secret? Sign-in inside wp-admin stops until you paste it in Agamsale.');">
			<input type="hidden" name="action" value="field_sales_regenerate_secret" />
			<?php wp_nonce_field('field_sales_regenerate_secret'); ?>
			<?php submit_button('Regenerate secret', 'secondary small', 'submit', false); ?>
		</form>
		<?php field_sales_render_site_hint($url); ?>

		<?php field_sales_render_payments_section(); ?>

		<h2>Connect this store</h2>
		<p>So the app can read products and push orders, give it API keys:</p>
		<ol>
			<li>WooCommerce → Settings → Advanced → <strong>REST API</strong> → Add key (permissions <strong>Read/Write</strong>).</li>
			<li>In the Agamsale app, open <strong>Store Connection</strong>: Platform <em>WooCommerce</em>, this store's
				address, the Consumer Key and Secret, then Save and <strong>Test Connection</strong>.</li>
		</ol>
	</div>
	<?php
}

/** Settings → Agamsale → Razorpay payments: tests, gateway and webhook status. */
function field_sales_render_payments_section() {
	$result = get_transient('field_sales_test_' . get_current_user_id());
	if ($result) {
		delete_transient('field_sales_test_' . get_current_user_id());
	}
	$gateway_url = admin_url('admin.php?page=wc-settings&tab=checkout&section=agamsale_razorpay');
	$gateway = get_option('woocommerce_agamsale_razorpay_settings', []);
	$status = $result['status'] ?? null;
	?>
	<h2 id="agamsale-payments">Razorpay payments</h2>
	<p>Customers can pay at checkout with Razorpay, and orders paid by Agamsale payment links can be refunded from
		the order screen. Agamsale keeps the Razorpay keys (Agamsale → Settings → Payments); WordPress never stores them.</p>
	<?php if ($result) : ?>
		<div class="notice notice-<?php echo $result['ok'] ? 'success' : 'error'; ?> inline">
			<p><strong><?php echo esc_html($result['what']); ?>:</strong> <?php echo esc_html($result['message']); ?></p>
		</div>
	<?php endif; ?>
	<table class="form-table" role="presentation">
		<tr>
			<th scope="row">Checkout gateway</th>
			<td>
				<?php echo (($gateway['enabled'] ?? 'no') === 'yes') ? '<strong>Enabled</strong>' : 'Disabled'; ?> —
				<a href="<?php echo esc_url($gateway_url); ?>">turn on or off, title and description</a>
			</td>
		</tr>
		<?php if ($status) : ?>
			<tr>
				<th scope="row">Razorpay in Agamsale</th>
				<td>
					<?php echo esc_html(($status['enabled'] ? 'On' : 'Off') . ', ' . $status['mode'] . ' mode, keys ' . ($status['keys_set'] ? 'set' : 'missing')); ?>
				</td>
			</tr>
			<tr>
				<th scope="row">Webhook</th>
				<td>
					<p>In the Razorpay Dashboard (Accounts &amp; Settings → Webhooks) add <code><?php echo esc_html($status['webhook_url']); ?></code>
						with the secret saved in Agamsale, for these events:</p>
					<p><code><?php echo esc_html(implode(', ', $status['webhook_events'])); ?></code></p>
					<p class="description">
						Secret <?php echo $status['webhook_secret_set'] ? 'set in Agamsale' : '<strong>not set in Agamsale</strong>'; ?>;
						last event received: <?php echo esc_html($status['last_webhook_at'] ?: 'never'); ?>.
					</p>
				</td>
			</tr>
		<?php endif; ?>
	</table>
	<p style="display:flex;gap:8px;">
		<?php foreach (['field_sales_test_connection' => 'Test connection', 'field_sales_test_razorpay' => 'Test Razorpay connection'] as $action => $label) : ?>
			<form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
				<input type="hidden" name="action" value="<?php echo esc_attr($action); ?>" />
				<?php wp_nonce_field($action); ?>
				<?php submit_button($label, 'secondary', 'submit', false); ?>
			</form>
		<?php endforeach; ?>
	</p>
	<?php
}

/** Same site (sign-in cookies work in the frame too) or not, and an HTTPS warning. */
function field_sales_render_site_hint(string $url) {
	if (!$url) {
		return;
	}
	$app_host = (string) wp_parse_url($url, PHP_URL_HOST);
	$wp_host = (string) wp_parse_url(admin_url(), PHP_URL_HOST);
	if (!$app_host || !$wp_host) {
		return;
	}
	if (is_ssl() && stripos($url, 'http://') === 0) {
		echo '<div class="notice notice-error inline"><p>wp-admin is on HTTPS but the App URL is <code>http://</code>: browsers block the frame. Serve Agamsale over HTTPS.</p></div>';
		return;
	}
	if (field_sales_site_of($app_host) === field_sales_site_of($wp_host)) {
		echo '<p class="description">The app is on the same site as this store (<code>' . esc_html(field_sales_site_of($wp_host)) . '</code>): its own sign-in also works inside the frame.</p>';
	} else {
		echo '<p class="description">The app (<code>' . esc_html($app_host) . '</code>) is on a different site from this store, so browsers block its own sign-in inside the frame; the secret above signs you in instead. '
			. 'Alternatively, serve Agamsale on a subdomain of this store (e.g. <code>sales.' . esc_html(field_sales_site_of($wp_host)) . '</code>).</p>';
	}
}

function field_sales_render_page() {
	$url = field_sales_url();
	if (!$url) {
		?>
		<div class="wrap">
			<h1>Agamsale</h1>
			<div class="notice notice-warning"><p>
				Set the app URL on <a href="<?php echo esc_url(admin_url('options-general.php?page=field-sales-settings')); ?>">Settings → Agamsale</a> first.
			</p></div>
		</div>
		<?php
		return;
	}
	// The submenu's page, or the page the frame last reported (kept in &path= so a reload or a
	// shared link reopens it).
	$pages = field_sales_pages();
	$slug = isset($_GET['page']) ? sanitize_key(wp_unslash($_GET['page'])) : 'field-sales';
	$path = isset($_GET['path']) ? field_sales_clean_path((string) wp_unslash($_GET['path'])) : '';
	if ($path === '') {
		$path = isset($pages[$slug]) ? $pages[$slug][1] : 'dashboard';
	}
	$frame_url = add_query_arg('wp_embed', '1', $url . '/' . $path);
	?>
	<div class="wrap" style="margin-right:0;">
		<h1 class="screen-reader-text">Agamsale</h1>
		<p style="display:flex;gap:8px;align-items:center;margin:8px 0;">
			<span><?php echo esc_html(wp_parse_url($url, PHP_URL_HOST)); ?></span>
			<a class="button button-small" href="<?php echo esc_url($url . '/' . $path); ?>" target="_blank" rel="noopener">
				Open in a new tab <span class="dashicons dashicons-external" style="font-size:14px;line-height:1.4;"></span>
			</a>
		</p>
		<!-- allow: check-in location, barcode scanner (camera) and copy buttons work inside the frame. -->
		<iframe id="field-sales-frame" src="<?php echo esc_url($frame_url); ?>"
			allow="geolocation; camera; clipboard-read; clipboard-write; fullscreen"
			style="width:100%;height:calc(100vh - 120px);border:1px solid #c3c4c7;border-radius:4px;background:#fff;"
			title="Agamsale"></iframe>
	</div>
	<script>
	// The app frame asks for a sign-in token ({type:'agamsale:token-request', id}); answer with a
	// fresh one from admin-ajax ({type:'agamsale:token', id, token, expires_in} or {..., error}).
	(function () {
		var frame = document.getElementById('field-sales-frame');
		var appOrigin = <?php echo wp_json_encode(field_sales_origin($url)); ?>;
		var ajaxUrl = <?php echo wp_json_encode(admin_url('admin-ajax.php')); ?>;
		var nonce = <?php echo wp_json_encode(wp_create_nonce('agamsale_token')); ?>;
		function reply(id, data) {
			data.type = 'agamsale:token';
			data.id = id;
			frame.contentWindow.postMessage(data, appOrigin);
		}
		// The frame reports each page it opens ({type:'agamsale:navigated', path, title}): keep it in
		// this page's URL and highlight the Agamsale submenu that owns it.
		var pages = <?php echo wp_json_encode(array_map(function ($p) { return $p[1]; }, $pages)); ?>;
		var owners = { 'sync-failures': 'field-sales-status', 'my-orders': 'field-sales' };
		var siteTitle = <?php echo wp_json_encode(get_bloginfo('name')); ?>;
		function onNavigated(msg) {
			var path = String(msg.path || '');
			if (!/^[A-Za-z0-9][A-Za-z0-9\/_.%~=&+,?-]*$/.test(path) || path.indexOf('..') !== -1 || path.indexOf('//') !== -1) return;
			var first = path.split(/[/?]/)[0];
			var slug = owners[first] || 'field-sales';
			Object.keys(pages).forEach(function (s) { if (pages[s] === first) slug = s; });
			var url = new URL(window.location.href);
			url.searchParams.set('page', slug);
			url.searchParams.set('path', path);
			window.history.replaceState(null, '', url.toString());
			document.querySelectorAll('#toplevel_page_field-sales .wp-submenu li').forEach(function (li) {
				var a = li.querySelector('a');
				var current = !!a && new URL(a.href, window.location.href).searchParams.get('page') === slug;
				li.classList.toggle('current', current);
				if (a) {
					a.classList.toggle('current', current);
					if (current) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
				}
			});
			if (msg.title) document.title = msg.title + ' \u2039 Agamsale \u2039 ' + siteTitle;
		}
		window.addEventListener('message', function (event) {
			var msg = event.data;
			if (event.source !== frame.contentWindow || event.origin !== appOrigin) return;
			if (msg && msg.type === 'agamsale:navigated') return onNavigated(msg);
			if (!msg || msg.type !== 'agamsale:token-request') return;
			var body = new URLSearchParams({ action: 'agamsale_token', _ajax_nonce: nonce });
			fetch(ajaxUrl, { method: 'POST', credentials: 'same-origin', body: body })
				.then(function (r) { return r.json(); })
				.then(function (res) {
					if (res && res.success) {
						reply(msg.id, { token: res.data.token, expires_in: res.data.expires_in });
					} else {
						reply(msg.id, { error: (res && res.data && res.data.message) || 'WordPress did not issue a sign-in token.' });
					}
				})
				.catch(function () {
					reply(msg.id, { error: 'Your WordPress session has expired. Reload this page.' });
				});
		});
	})();
	</script>
	<?php
}
