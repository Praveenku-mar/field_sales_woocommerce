<?php
/**
 * Plugin Name: Agamsale
 * Description: Run the Agamsale app (visits, orders, customers) inside WooCommerce. Connect it once on Settings → Agamsale.
 * Version: 1.1.0
 * Author: Agamsale
 * Requires at least: 6.0
 * Requires PHP: 7.4
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
	$frame_url = add_query_arg('wp_embed', '1', $url);
	?>
	<div class="wrap" style="margin-right:0;">
		<h1 class="screen-reader-text">Agamsale</h1>
		<p style="display:flex;gap:8px;align-items:center;margin:8px 0;">
			<span><?php echo esc_html(wp_parse_url($url, PHP_URL_HOST)); ?></span>
			<a class="button button-small" href="<?php echo esc_url($url); ?>" target="_blank" rel="noopener">
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
		window.addEventListener('message', function (event) {
			var msg = event.data;
			if (event.source !== frame.contentWindow || event.origin !== appOrigin) return;
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
