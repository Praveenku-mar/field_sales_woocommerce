<?php
/**
 * Server-to-server calls from this WordPress site to Agamsale (Frappe):
 * field_sales_woocommerce.payments.checkout.* (ping, test_razorpay, create_checkout_order,
 * verify_checkout_payment, refund_checkout_payment).
 *
 * Each request is signed with the secret this plugin shares with Agamsale (Settings → Agamsale):
 *   X-Agamsale-Timestamp: unix seconds
 *   X-Agamsale-Nonce:     32 random hex characters, used once
 *   X-Agamsale-Signature: hex HMAC-SHA256("{timestamp}\n{nonce}\n{path}\n{body}", secret)
 * Agamsale refuses requests older than 5 minutes or with a nonce it has seen. No Razorpay key
 * or Agamsale API key is stored in WordPress.
 */

if (!defined('ABSPATH')) {
	exit;
}

final class Agamsale_Api {
	const METHOD_PREFIX = '/api/method/field_sales_woocommerce.payments.checkout.';
	const TIMEOUT = 20;
	// Order creation and verification are safe to repeat, so a network failure or a
	// 502/503/504 is retried once with a new signature. Refunds are not: if Razorpay refunded
	// but the answer was lost, a retry could refund twice, so the admin retries by hand.
	const RETRY_STATUSES = [502, 503, 504];
	const NO_RETRY = ['refund_checkout_payment'];

	/** Agamsale's server: the setting, else scheme://host[:port] of the App URL. */
	public static function base_url(): string {
		$url = trim((string) get_option(FIELD_SALES_FRAPPE_URL_OPTION, ''));
		if ($url === '') {
			$url = field_sales_origin(field_sales_url());
		}
		return rtrim($url, '/');
	}

	public static function is_configured(): bool {
		return self::base_url() !== '' && strlen((string) get_option(FIELD_SALES_SECRET_OPTION, '')) >= 32;
	}

	/**
	 * Call an endpoint; returns the decoded answer (always has 'ok' => true) or a WP_Error whose
	 * code is Agamsale's (e.g. NOT_PAYABLE, INVALID_SIGNATURE) or AGAMSALE_UNREACHABLE.
	 *
	 * @return array|WP_Error
	 */
	public static function call(string $method, array $body = []) {
		if (!self::is_configured()) {
			return new WP_Error('NOT_CONFIGURED', 'Agamsale is not connected. Set the App URL on Settings → Agamsale.');
		}
		$result = self::send($method, $body);
		if (!in_array($method, self::NO_RETRY, true) && self::should_retry($result)) {
			usleep(500000);
			$result = self::send($method, $body);
		}
		return $result;
	}

	private static function should_retry($result): bool {
		if (!is_wp_error($result)) {
			return false;
		}
		$data = $result->get_error_data();
		$status = is_array($data) && isset($data['status']) ? (int) $data['status'] : 0;
		return $result->get_error_code() === 'AGAMSALE_UNREACHABLE' || in_array($status, self::RETRY_STATUSES, true);
	}

	/** @return array|WP_Error */
	private static function send(string $method, array $body) {
		$path = self::METHOD_PREFIX . $method;
		$raw = wp_json_encode((object) $body);
		$timestamp = (string) time();
		$nonce = bin2hex(random_bytes(16));
		$signature = hash_hmac('sha256', $timestamp . "\n" . $nonce . "\n" . $path . "\n" . $raw, field_sales_secret());

		$headers = [
			'Content-Type' => 'application/json',
			'Accept' => 'application/json',
			'X-Agamsale-Timestamp' => $timestamp,
			'X-Agamsale-Nonce' => $nonce,
			'X-Agamsale-Signature' => $signature,
		];
		$site = trim((string) get_option(FIELD_SALES_FRAPPE_SITE_OPTION, ''));
		if ($site !== '') {
			// When the server URL does not name the Frappe site (e.g. a Docker host address).
			$headers['X-Frappe-Site-Name'] = $site;
		}

		$response = wp_remote_post(self::base_url() . $path, [
			'timeout' => self::TIMEOUT,
			'headers' => $headers,
			'body' => $raw,
			'data_format' => 'body',
		]);
		if (is_wp_error($response)) {
			// No details from the transport in the message: it can include internal addresses.
			return new WP_Error('AGAMSALE_UNREACHABLE', 'Could not reach Agamsale. Try again in a moment.', ['status' => 0]);
		}

		$status = (int) wp_remote_retrieve_response_code($response);
		$json = json_decode((string) wp_remote_retrieve_body($response), true);
		// Frappe wraps a whitelisted method's return value in {"message": ...}.
		$answer = is_array($json) && isset($json['message']) && is_array($json['message']) ? $json['message'] : null;
		if (is_array($answer) && !empty($answer['ok'])) {
			return $answer;
		}
		if (is_array($answer) && isset($answer['code'])) {
			return new WP_Error((string) $answer['code'], (string) ($answer['message'] ?? 'Agamsale refused the request.'), ['status' => $status]);
		}
		return new WP_Error(
			$status >= 500 || $status === 0 ? 'AGAMSALE_UNREACHABLE' : 'AGAMSALE_ERROR',
			sprintf('Agamsale answered HTTP %d. Check the server URL and that the app is up to date.', $status),
			['status' => $status]
		);
	}
}
