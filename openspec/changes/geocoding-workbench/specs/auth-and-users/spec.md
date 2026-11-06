## ADDED Requirements

### Requirement: Username and password login
The system SHALL authenticate users with a username and password. Passwords MUST be stored only as Argon2id hashes. Successful login SHALL establish a session delivered as an HttpOnly, Secure, SameSite=Lax cookie; the session token MUST NOT be readable by frontend JavaScript.

#### Scenario: Successful login
- **WHEN** an active user submits a correct username and password
- **THEN** the system sets a session cookie and returns the user's id, username and role

#### Scenario: Wrong credentials
- **WHEN** a user submits an unknown username or a wrong password
- **THEN** the system responds with 401 and the same generic error message in both cases

#### Scenario: Deactivated account
- **WHEN** a deactivated user submits correct credentials
- **THEN** the system responds with 401 and does not create a session

### Requirement: Login brute-force protection
The system SHALL rate-limit failed login attempts per username and per client IP.

#### Scenario: Too many failed attempts
- **WHEN** a username accumulates 10 failed login attempts within 15 minutes
- **THEN** further login attempts for that username are rejected with 429 until the window expires

### Requirement: Logout and session expiry
The system SHALL allow users to log out, invalidating the session server-side. Sessions SHALL expire after a configurable idle timeout (default 8 hours).

#### Scenario: Logout
- **WHEN** a logged-in user logs out
- **THEN** the session is invalidated and subsequent requests with the old cookie receive 401

#### Scenario: Idle expiry
- **WHEN** a session has been idle longer than the configured timeout
- **THEN** requests using it receive 401 and the frontend redirects to the login page

### Requirement: Roles
The system SHALL support exactly two roles: `user` and `admin`. All API endpoints except login and health MUST require an authenticated session.

#### Scenario: Unauthenticated request
- **WHEN** a request without a valid session calls any protected endpoint
- **THEN** the system responds with 401

#### Scenario: Non-admin calls admin endpoint
- **WHEN** a user with role `user` calls a user-management endpoint
- **THEN** the system responds with 403

### Requirement: Admin user management
Admins SHALL be able to create users (username, initial password, role), change a user's role, reset a user's password, and deactivate or reactivate users. Admins MUST NOT be able to deactivate or demote the last active admin.

#### Scenario: Admin creates user
- **WHEN** an admin creates a user with a unique username, a password meeting the policy and a role
- **THEN** the user can log in with those credentials

#### Scenario: Duplicate username
- **WHEN** an admin creates a user whose username already exists (case-insensitive)
- **THEN** the system responds with 409

#### Scenario: Protect last admin
- **WHEN** an admin tries to deactivate or demote the only remaining active admin
- **THEN** the system responds with 409 and makes no change

### Requirement: Password policy and self-service change
Passwords SHALL be at least 12 characters. Users SHALL be able to change their own password by providing the current password. A password reset by an admin SHALL force the user to set a new password at next login.

#### Scenario: Weak password rejected
- **WHEN** a password shorter than 12 characters is submitted
- **THEN** the system responds with 422 describing the policy

#### Scenario: Forced change after reset
- **WHEN** a user logs in after an admin reset their password
- **THEN** the system requires a password change before any other endpoint can be used

### Requirement: Initial admin bootstrap
The system SHALL provide a CLI command that creates the first admin account when no admin exists.

#### Scenario: Bootstrap on empty system
- **WHEN** the bootstrap command runs with a username and password and no admin exists
- **THEN** an active admin account is created

#### Scenario: Bootstrap refused when admin exists
- **WHEN** the bootstrap command runs and an admin already exists
- **THEN** the command exits with an error and creates nothing

### Requirement: Job ownership and visibility
Every job SHALL be owned by the user who created it. A `user` SHALL see and act only on their own jobs. An `admin` SHALL see and act on all jobs.

#### Scenario: User accesses another user's job
- **WHEN** a `user` requests a job, its rows or its export that they do not own
- **THEN** the system responds with 404

#### Scenario: Admin lists all jobs
- **WHEN** an admin lists jobs
- **THEN** jobs from all users are returned, each with its owner's username
