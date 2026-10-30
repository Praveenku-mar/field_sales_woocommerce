<?php
/**
 * Plugin Name: Field Sales
 * Description: Run the Field Sales app (visits, orders, customers) inside WooCommerce. Connect it once on Settings → Field Sales.
 * Version: 1.0.0
 * Author: Field Sales
 * Requires at least: 6.0
 * Requires PHP: 7.4
 */

if (!defined('ABSPATH')) {
	exit;
}

const FIELD_SALES_OPTION = 'field_sales_app_url';
const FIELD_SALES_CAP = 'manage_woocommerce';

function field_sales_url(): string {
	$url = trim((string) get_option(FIELD_SALES_OPTION, ''));
	return $url ? rtrim($url, '/') : '';
}

function field_sales_configured(): bool {
	return (bool) field_sales_url();
}

add_action('admin_menu', function () {
	add_menu_page(
		'Field Sales',
		'Field Sales',
		FIELD_SALES_CAP,
		'field-sales',
		'field_sales_render_page',
		'dashicons-store',
		56
	);
	add_options_page(
		'Field Sales',
		'Field Sales',
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

function field_sales_render_settings() {
	?>
	<div class="wrap">
		<h1>Field Sales</h1>
		<p>Embed the Field Sales app in your WooCommerce admin.</p>
		<form method="post" action="options.php">
			<?php settings_fields('field_sales'); ?>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="<?php echo esc_attr(FIELD_SALES_OPTION); ?>">App URL</label></th>
					<td>
						<input type="url" class="regular-text code" id="<?php echo esc_attr(FIELD_SALES_OPTION); ?>"
							name="<?php echo esc_attr(FIELD_SALES_OPTION); ?>"
							value="<?php echo esc_attr(field_sales_url()); ?>"
							placeholder="https://sales.example.com/fieldsales" />
						<p class="description">
							Where your Field Sales app is served, including <code>/fieldsales</code>.
							Sign in with your Field Sales user (Sales Rep, Sales Manager or System Manager).
						</p>
					</td>
				</tr>
			</table>
			<?php submit_button(); ?>
		</form>
		<h2>Connect this store</h2>
		<p>So the app can read products and push orders, give it API keys:</p>
		<ol>
			<li>WooCommerce → Settings → Advanced → <strong>REST API</strong> → Add key (permissions <strong>Read/Write</strong>).</li>
			<li>In the Field Sales app, open <strong>Store Connection</strong>: Platform <em>WooCommerce</em>, this store's
				address, the Consumer Key and Secret, then Save and <strong>Test Connection</strong>.</li>
		</ol>
	</div>
	<?php
}

function field_sales_render_page() {
	$url = field_sales_url();
	if (!$url) {
		?>
		<div class="wrap">
			<h1>Field Sales</h1>
			<div class="notice notice-warning"><p>
				Set the app URL on <a href="<?php echo esc_url(admin_url('options-general.php?page=field-sales-settings')); ?>">Settings → Field Sales</a> first.
			</p></div>
		</div>
		<?php
		return;
	}
	?>
	<div class="wrap" style="margin-right:0;">
		<h1 class="screen-reader-text">Field Sales</h1>
		<p style="display:flex;gap:8px;align-items:center;margin:8px 0;">
			<span><?php echo esc_html(parse_url($url, PHP_URL_HOST)); ?></span>
			<a class="button button-small" href="<?php echo esc_url($url); ?>" target="_blank" rel="noopener">
				Open in a new tab <span class="dashicons dashicons-external" style="font-size:14px;line-height:1.4;"></span>
			</a>
			<span class="description">If the app asks you to sign in repeatedly inside this page, use the new tab — browsers
				can restrict sign-in inside frames.</span>
		</p>
		<iframe id="field-sales-frame" src="<?php echo esc_url($url); ?>"
			style="width:100%;height:calc(100vh - 120px);border:1px solid #c3c4c7;border-radius:4px;background:#fff;"
			title="Field Sales"></iframe>
	</div>
	<?php
}
