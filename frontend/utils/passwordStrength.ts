/**
 * Shared password strength read-out for the set-password and change-password
 * forms. One implementation so the two screens cannot disagree about what
 * "Strong" means, and so the minimum length matches the server's
 * MIN_PASSWORD_LENGTH in routes/user_password.py.
 *
 * This is guidance for the person typing, not a gate — the only hard rule is
 * the length, and that is enforced server-side.
 */

export const MIN_PASSWORD_LENGTH = 8

export interface PasswordStrength {
    /** 0–4, how many bars to fill. */
    score: number
    label: string
    barClass: string
    textClass: string
}

type Translate = (key: string) => string

export function passwordStrength(value: string, t?: Translate): PasswordStrength {
    const tr = (key: string, fallback: string) => {
        if (!t) return fallback
        const out = t(key)
        // A missing key renders as the key itself; fall back rather than show it.
        return out === key ? fallback : out
    }

    if (!value) {
        return { score: 0, label: ' ', barClass: '', textClass: 'text-gray-400 dark:text-gray-500' }
    }

    if (value.length < MIN_PASSWORD_LENGTH) {
        return {
            score: 1,
            label: tr('password.tooShort', `Too short — at least ${MIN_PASSWORD_LENGTH} characters.`),
            barClass: 'bg-red-400',
            textClass: 'text-red-600',
        }
    }

    let score = 1
    if (value.length >= 12) score++
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score++
    if (/[0-9]/.test(value) && /[^A-Za-z0-9]/.test(value)) score++
    score = Math.min(score, 4)

    if (score <= 2) {
        return {
            score,
            label: tr('password.fair', 'Fair'),
            barClass: 'bg-amber-400',
            textClass: 'text-amber-600',
        }
    }
    return {
        score,
        label: score === 3 ? tr('password.good', 'Good') : tr('password.strong', 'Strong'),
        barClass: 'bg-green-500',
        textClass: 'text-green-600',
    }
}
