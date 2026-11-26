import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import { renderWithProviders } from '../../test/utils'
import { setTextSize } from '../../utils/textSize'
import { UserMenu } from './UserMenu'

describe('Text size', () => {
  afterEach(() => setTextSize('default'))

  it('scales the whole interface from the avatar menu and is remembered', async () => {
    useAuthStore.setState({ status: 'authenticated', accessToken: 't', user: { id: 'u', email: 'a@b.c', display_name: 'Ada', is_active: true, created_at: '' } })
    renderWithProviders(<UserMenu />)
    await userEvent.click(screen.getByTitle('Ada'))
    expect(screen.getByRole('radio', { name: 'M' })).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(screen.getByRole('radio', { name: 'XL' }))
    expect(document.documentElement.style.fontSize).toBe('125%')
    expect(localStorage.getItem('forma3d.textSize')).toBe('xlarge')
    expect(screen.getByRole('radio', { name: 'XL' })).toHaveAttribute('aria-checked', 'true')
  })
})
