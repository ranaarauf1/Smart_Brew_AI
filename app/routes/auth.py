"""Account pages: register, login, logout, profile and session keepalive (feature 1)."""

from functools import wraps

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from app.services import auth

auth_bp = Blueprint("auth", __name__)


def login_required(view):
    """
    Purpose: Decorator that sends guests to the login page.

    Args:
        view (callable): The view function to protect.

    Returns:
        callable: The wrapped view.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        """
        Purpose: Runs the view only when a user is logged in.

        Args:
            *args: Positional URL arguments for the view.
            **kwargs: Keyword URL arguments for the view.

        Returns:
            Response: The view's response, or a redirect to the login page.
        """
        if g.user is None:
            flash("Please log in first.", "warning")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


@auth_bp.before_app_request
def load_user_and_check_timeout():
    """
    Purpose: Before every request, ends sessions inactive for more than 60 s
        (NFR-14) and loads the logged-in user into g.user.

    Args:
        None

    Returns:
        Response | None: A 401 JSON response for an expired keepalive call,
            otherwise None so the request continues.
    """
    g.user = None
    g.session_timed_out = False
    if request.endpoint == "static":
        return None

    if auth.is_session_expired():
        auth.end_session()
        g.session_timed_out = True
        if request.endpoint == "auth.keepalive":
            return jsonify(logged_in=False), 401
        flash(auth.TIMEOUT_MESSAGE, "warning")
        return None

    g.user = auth.get_session_user()
    auth.touch_session()
    return None


@auth_bp.app_context_processor
def inject_session_info():
    """
    Purpose: Makes the current user and timeout available in every template.

    Args:
        None

    Returns:
        dict: Template variables current_user and session_timeout.
    """
    return {
        "current_user": g.get("user"),
        "session_timeout": auth.session_timeout_seconds(),
    }


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """
    Purpose: Shows the create-account form and creates the account (FR-01).
        A successful registration logs the user in.

    Args:
        None

    Returns:
        Response: The form (with errors, status 400) or a redirect to the menu.
    """
    if g.user is not None:
        return redirect(url_for("customer.home"))

    if request.method == "POST":
        user, errors = auth.register_user(
            request.form.get("name"),
            request.form.get("email"),
            request.form.get("password"),
        )
        if errors:
            return render_template("register.html", errors=errors, form=request.form), 400
        auth.start_session(user)
        flash(f"Welcome, {user.name}! Your account has been created.", "success")
        return redirect(url_for("customer.menu"))

    return render_template("register.html", errors={}, form={})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Purpose: Shows the login form and logs the user in by email and password
        (FR-02).

    Args:
        None

    Returns:
        Response: The form (with an error, status 401) or a redirect to the menu.
    """
    if g.user is not None:
        return redirect(url_for("customer.home"))

    if request.method == "POST":
        user = auth.authenticate(request.form.get("email"), request.form.get("password"))
        if user is None:
            return render_template(
                "login.html", error=auth.LOGIN_FAILED_MESSAGE, form=request.form
            ), 401
        auth.start_session(user)
        flash(f"Welcome back, {user.name}!", "success")
        return redirect(url_for("customer.menu"))

    return render_template("login.html", error=None, form={})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """
    Purpose: Logs the user out, by the Log out button or by the countdown
        reaching zero (FR-03, NFR-14).

    Args:
        None

    Returns:
        Response: Redirect to the home page.
    """
    if g.session_timed_out:
        # The request hook already ended the session and flashed the timeout
        # message; clearing again would delete that message.
        return redirect(url_for("customer.home"))

    was_logged_in = g.user is not None
    auth.end_session()

    if request.form.get("reason") == "timeout":
        flash(auth.TIMEOUT_MESSAGE, "warning")
    elif was_logged_in:
        flash("You have been logged out.", "success")
    return redirect(url_for("customer.home"))


@auth_bp.route("/auth/keepalive", methods=["POST"])
def keepalive():
    """
    Purpose: Called by the countdown script when the user taps the screen,
        so activity without page loads still counts (NFR-14).

    Args:
        None

    Returns:
        Response: 204 if the session is alive, 401 JSON if logged out.
    """
    if g.user is None:
        return jsonify(logged_in=False), 401
    return "", 204


@auth_bp.route("/profile")
@login_required
def profile():
    """
    Purpose: Shows the user's name, linked NFC card and order history.

    Args:
        None

    Returns:
        str: The rendered profile page.
    """
    return render_template(
        "profile.html",
        linked_card=auth.get_linked_card(g.user),
        linkable_cards=auth.get_linkable_cards(),
        orders=auth.get_order_history(g.user),
    )


@auth_bp.route("/profile/link-card", methods=["POST"])
@login_required
def link_card():
    """
    Purpose: Links the chosen demo NFC card to the logged-in user.

    Args:
        None

    Returns:
        Response: Redirect back to the profile page with a message.
    """
    card_id = request.form.get("card_id", type=int)
    try:
        if card_id is None:
            raise auth.CardLinkError("Please choose a card.")
        card = auth.link_card(g.user, card_id)
    except auth.CardLinkError as error:
        flash(str(error), "error")
    else:
        flash(f"Card {card.uid} is now linked to your account.", "success")
    return redirect(url_for("auth.profile"))


@auth_bp.route("/profile/unlink-card", methods=["POST"])
@login_required
def unlink_card():
    """
    Purpose: Removes the NFC card link from the logged-in user.

    Args:
        None

    Returns:
        Response: Redirect back to the profile page with a message.
    """
    if auth.unlink_card(g.user):
        flash("Your card has been unlinked.", "success")
    else:
        flash("No card was linked.", "warning")
    return redirect(url_for("auth.profile"))
