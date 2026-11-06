import { AppShell, Badge, Button, Group, Menu, Text, UnstyledButton, useMantineColorScheme } from '@mantine/core'
import { IconChevronDown, IconLogout, IconMapPin, IconMoon, IconSun } from '@tabler/icons-react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useCapabilities } from '../lib/hooks'

export function AppLayout() {
  const { me, logout } = useAuth()
  const caps = useCapabilities()
  const { colorScheme, toggleColorScheme } = useMantineColorScheme()
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="lg">
            <UnstyledButton component={Link} to="/">
              <Group gap={6}>
                <IconMapPin size={22} color="var(--mantine-primary-color-filled)" />
                <Text fw={700}>Geocoding Workbench</Text>
              </Group>
            </UnstyledButton>
            <Group gap={4}>
              <Button component={NavLink} to="/" variant="subtle" size="compact-sm">
                Jobs
              </Button>
              <Button component={NavLink} to="/distance" variant="subtle" size="compact-sm">
                Distance
              </Button>
              {me?.role === 'admin' && (
                <>
                  <Button component={NavLink} to="/admin/users" variant="subtle" size="compact-sm">
                    Users
                  </Button>
                  <Button component={NavLink} to="/admin/scoring" variant="subtle" size="compact-sm">
                    Scoring
                  </Button>
                </>
              )}
            </Group>
          </Group>
          <Group gap="xs">
            {caps.data && !caps.data.street_geocoding_available && (
              <Badge color="yellow" variant="light" title="No geocoder API key configured">
                Street-level geocoding unavailable
              </Badge>
            )}
            {caps.data && !caps.data.ai_available && (
              <Badge color="gray" variant="light" title="No OpenAI API key configured">
                AI repair off
              </Badge>
            )}
            <Button variant="subtle" size="compact-sm" onClick={toggleColorScheme} aria-label="Toggle color scheme">
              {colorScheme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
            </Button>
            <Menu position="bottom-end">
              <Menu.Target>
                <Button variant="default" size="compact-sm" rightSection={<IconChevronDown size={14} />}>
                  {me?.username}
                  {me?.role === 'admin' && (
                    <Badge size="xs" ml={6} variant="light">
                      admin
                    </Badge>
                  )}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item component={Link} to="/change-password">
                  Change password
                </Menu.Item>
                <Menu.Item leftSection={<IconLogout size={14} />} onClick={() => void logout()}>
                  Sign out
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}
