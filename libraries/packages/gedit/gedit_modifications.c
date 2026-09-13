/*
 * Azzio gedit "notepad mode" plugin.
 *
 * gedit 50 is the gedit-technology fork (GTK3, libgedit-amtk/tepl). Azzio wants gedit
 * to feel like classic Windows Notepad: NO multi-tab feature, a headerbar stripped down
 * to only the hamburger menu + the window controls, and Ctrl+W that straight-EXITS the
 * app. NONE of that is reachable through config files, GSettings, GTK CSS or an accels
 * file on this fork (verified: GTK3 CSS cannot hide a GtkButton; there is no headerbar
 * GSetting; the accels file only feeds the legacy GtkAccelMap, not GAction accels; and
 * the fork DROPPED Python plugin support in 49.0). The ONLY supported hook that can do
 * it is a compiled libpeas plugin implementing GeditWindowActivatable, whose activate()
 * fires once per window with the GeditWindow handed in as the construct property
 * "window". So this small C plugin does exactly three things per window:
 *
 *   1. Kill the New Tab feature. Disable (and remove the accel of) the win.new-tab
 *      GAction so the "+" button goes dead and Ctrl+T does nothing -- combined with the
 *      launcher's --standalone --new-window and show-tabs-mode='never', opening a file is
 *      always a NEW WINDOW, never a tab.
 *   2. Strip the headerbar. Hide the New Tab "+" button, the Save button, and the whole
 *      linked Open box (the "Open" button AND its open-recent dropdown arrow), leaving
 *      ONLY the hamburger menu button and the window controls (min/max/close). We do this
 *      by walking the GtkHeaderBar children and matching the buttons' GAction names
 *      (win.new-tab / win.save / win.open) -- robust to layout shuffles, and we never
 *      touch the hamburger (win.hamburger-menu) or the window-control titlebuttons.
 *   3. Make Ctrl+W exit. On this fork win.close closes the current document and LEAVES an
 *      empty window (a "double exit"); only the WM close button or app.quit terminate the
 *      process. So we rebind: app.quit gets <Primary>W (and keeps <Primary>Q), and
 *      win.close's accel is cleared. Now Ctrl+W quits the app outright -- close = close.
 *
 * Built as /usr/lib/gedit/plugins/libgedit-modifications.so (+ gedit-modifications.plugin) and
 * enabled via the org.gnome.gedit.plugins active-plugins gschema override. Pure GTK3 +
 * Gio + the public gedit plugin API (gedit-window-activatable.h, gedit-window.h). No
 * private gedit symbols, so a gedit point-release will not break the link.
 */

#include <libpeas/peas.h>
#include <gedit/gedit-window-activatable.h>
#include <gedit/gedit-window.h>
#include <gtk/gtk.h>
#include <gio/gio.h>
#include <gmodule.h>

#define GEDIT_TYPE_MODIFICATIONS_PLUGIN (gedit_modifications_plugin_get_type ())

/*
 * The plugin object. It holds the GeditWindow it was activated for (delivered via the
 * construct-only "window" property that GeditWindowActivatable defines) and implements
 * the interface's activate/deactivate/update_state vfuncs.
 */
struct _GeditModificationsPlugin
{
	GObject parent_instance;
	GeditWindow *window;   /* construct property "window" (not owned; weak use only) */
};

typedef struct _GeditModificationsPlugin GeditModificationsPlugin;

typedef struct _GeditModificationsPluginClass
{
	GObjectClass parent_class;
} GeditModificationsPluginClass;

static void gedit_modifications_plugin_window_activatable_iface_init (GeditWindowActivatableInterface *iface);

/*
 * A libpeas plugin type must be registered DYNAMICALLY against the GTypeModule libpeas
 * hands us (so the type can be loaded/unloaded with the module), not statically. So we use
 * G_DEFINE_DYNAMIC_TYPE_EXTENDED, which generates gedit_modifications_plugin_register_type()
 * (called from peas_register_types below) instead of a get_type() that self-registers on
 * first use. This is the exact pattern gedit's own C plugins use.
 */
G_DEFINE_DYNAMIC_TYPE_EXTENDED (
	GeditModificationsPlugin,
	gedit_modifications_plugin,
	G_TYPE_OBJECT,
	0,
	G_IMPLEMENT_INTERFACE_DYNAMIC (GEDIT_TYPE_WINDOW_ACTIVATABLE,
	                               gedit_modifications_plugin_window_activatable_iface_init))

/* The construct property id for "window" (overridden from the interface). */
enum
{
	PROP_0,
	PROP_WINDOW
};

/* The window-scoped GActions whose headerbar buttons we hide. win.open covers the whole
 * linked Open box (button + open-recent dropdown); win.save the Save button; win.new-tab
 * the "+" button. The hamburger (win.hamburger-menu) and window-control titlebuttons are
 * deliberately NOT listed, so they stay. */
static const char *AZZIO_HIDDEN_BUTTON_ACTIONS[] = {
	"win.open",
	"win.save",
	"win.new-tab",
	NULL
};

/* Does `name` match any action in AZZIO_HIDDEN_BUTTON_ACTIONS? */
static gboolean
azzio_action_is_hidden (const char *name)
{
	int i;

	if (name == NULL)
	{
		return FALSE;
	}
	for (i = 0; AZZIO_HIDDEN_BUTTON_ACTIONS[i] != NULL; i++)
	{
		if (g_strcmp0 (name, AZZIO_HIDDEN_BUTTON_ACTIONS[i]) == 0)
		{
			return TRUE;
		}
	}
	return FALSE;
}

/*
 * Recursively walk a headerbar subtree and hide every actionable widget whose GAction is
 * one of the buttons we strip. When a hidden button lives inside a linked GtkBox (the
 * Open button + its open-recent dropdown share one .linked box), we hide the WHOLE box so
 * the dropdown arrow disappears with the button -- otherwise a lone arrow would remain.
 * The hamburger (win.hamburger-menu) and the window-control titlebuttons carry actions we
 * never match, so this leaves them untouched.
 */
static void
azzio_strip_headerbar_widget (GtkWidget *widget)
{
	if (GTK_IS_ACTIONABLE (widget))
	{
		const char *action = gtk_actionable_get_action_name (GTK_ACTIONABLE (widget));

		if (azzio_action_is_hidden (action))
		{
			/* If this button sits in a linked box (Open + dropdown), hide the box so the
			 * dropdown arrow goes too; else hide the button itself. */
			GtkWidget *parent = gtk_widget_get_parent (widget);

			if (GTK_IS_BOX (parent))
			{
				GtkStyleContext *ctx = gtk_widget_get_style_context (parent);

				if (gtk_style_context_has_class (ctx, "linked"))
				{
					gtk_widget_hide (parent);
					gtk_widget_set_no_show_all (parent, TRUE);
					return;
				}
			}
			gtk_widget_hide (widget);
			gtk_widget_set_no_show_all (widget, TRUE);
			return;
		}
	}

	/* Recurse into containers (but not into a box we just hid -- handled by the return
	 * above). */
	if (GTK_IS_CONTAINER (widget))
	{
		GList *children = gtk_container_get_children (GTK_CONTAINER (widget));
		GList *l;

		for (l = children; l != NULL; l = l->next)
		{
			azzio_strip_headerbar_widget (GTK_WIDGET (l->data));
		}
		g_list_free (children);
	}
}

/*
 * Disable the win.new-tab GAction (so the "+" button is inert and Ctrl+T is a no-op) and
 * clear its accelerator. The action is on the window's map; the accel is on the running
 * default GApplication (the window's application may not be set yet at activate() time).
 */
static void
azzio_kill_new_tab_action (GtkApplicationWindow *win)
{
	GAction *action = g_action_map_lookup_action (G_ACTION_MAP (win), "new-tab");
	GApplication *app;

	if (action != NULL && G_IS_SIMPLE_ACTION (action))
	{
		g_simple_action_set_enabled (G_SIMPLE_ACTION (action), FALSE);
	}
	app = g_application_get_default ();
	if (GTK_IS_APPLICATION (app))
	{
		const char *none[] = { NULL };
		gtk_application_set_accels_for_action (GTK_APPLICATION (app), "win.new-tab", none);
	}
}

/*
 * Our replacement win.close handler: destroy the window. On this fork gedit's own
 * win.close only closes the current DOCUMENT and leaves an empty window (the "double
 * exit" the user hates); destroying the window instead behaves exactly like the WM close
 * button, which -- since gedit never holds the GApplication beyond its windows -- drops
 * the last window and the process exits cleanly. With --standalone (one window == one
 * process) this is a straight EXIT: close = close.
 */
static void
azzio_close_activate (GSimpleAction *action,
                       GVariant      *parameter,
                       gpointer       user_data)
{
	GtkWidget *window = GTK_WIDGET (user_data);

	(void) action;
	(void) parameter;
	if (window != NULL && GTK_IS_WIDGET (window))
	{
		gtk_widget_destroy (window);
	}
}

/*
 * Make Ctrl+W exit the application (close = close, no leftover empty window). Two belts:
 *
 *   1. REPLACE the win.close action on the window with our own that DESTROYS the window
 *      (see azzio_close_activate). This is window-scoped and immune to app-accel timing:
 *      whatever accel maps to win.close (Ctrl+W) now quits. gedit's win.close is a stateful
 *      action added at window construction; we remove it and add ours in its place.
 *   2. Also move the app-level accels so app.quit answers <Primary>W too (belt-and-braces
 *      in case something re-adds win.close). Uses the running default GApplication, since
 *      the window's application may not be set yet at activate() time.
 */
static void
azzio_rebind_close_to_quit (GtkApplicationWindow *win)
{
	GApplication *app;

	/* 1. Window-scoped: swap win.close for our destroy-the-window action. */
	g_action_map_remove_action (G_ACTION_MAP (win), "close");
	{
		GSimpleAction *close_action = g_simple_action_new ("close", NULL);
		g_signal_connect (close_action, "activate",
		                  G_CALLBACK (azzio_close_activate), win);
		g_action_map_add_action (G_ACTION_MAP (win), G_ACTION (close_action));
		g_object_unref (close_action);
	}

	/* 2. App-scoped belt-and-braces (default GApplication is the running gedit app). */
	app = g_application_get_default ();
	if (GTK_IS_APPLICATION (app))
	{
		const char *quit_accels[] = { "<Primary>W", "<Primary>Q", NULL };
		const char *close_accels[] = { "<Primary>W", NULL };

		gtk_application_set_accels_for_action (GTK_APPLICATION (app), "app.quit", quit_accels);
		/* Keep Ctrl+W mapped to win.close too (our replacement action quits), so the accel
		 * fires our destroy handler even if app.quit's binding is shadowed. */
		gtk_application_set_accels_for_action (GTK_APPLICATION (app), "win.close", close_accels);
	}
}

/* GeditWindowActivatable::activate -- runs once per window when the plugin is enabled. */
static void
gedit_modifications_plugin_activate (GeditWindowActivatable *activatable)
{
	GeditModificationsPlugin *plugin = (GeditModificationsPlugin *) activatable;
	GtkWidget *titlebar;

	g_return_if_fail (plugin->window != NULL);

	/* 1. Kill the New Tab feature. */
	azzio_kill_new_tab_action (GTK_APPLICATION_WINDOW (plugin->window));

	/* 2. Strip the headerbar down to hamburger + window controls. */
	titlebar = gtk_window_get_titlebar (GTK_WINDOW (plugin->window));
	if (titlebar != NULL)
	{
		azzio_strip_headerbar_widget (titlebar);
	}

	/* 3. Ctrl+W exits the app (close = close, no leftover empty window). */
	azzio_rebind_close_to_quit (GTK_APPLICATION_WINDOW (plugin->window));
}

static void
gedit_modifications_plugin_deactivate (GeditWindowActivatable *activatable)
{
	/* Nothing to undo: the process is a per-file --standalone instance that exits when
	 * its window closes, and the header buttons/actions are re-created per window. Leaving
	 * this a no-op keeps the plugin simple and side-effect free on teardown. */
	(void) activatable;
}

static void
gedit_modifications_plugin_update_state (GeditWindowActivatable *activatable)
{
	(void) activatable;
}

static void
gedit_modifications_plugin_window_activatable_iface_init (GeditWindowActivatableInterface *iface)
{
	iface->activate = gedit_modifications_plugin_activate;
	iface->deactivate = gedit_modifications_plugin_deactivate;
	iface->update_state = gedit_modifications_plugin_update_state;
}

static void
gedit_modifications_plugin_set_property (GObject      *object,
                                    guint         prop_id,
                                    const GValue *value,
                                    GParamSpec   *pspec)
{
	GeditModificationsPlugin *plugin = (GeditModificationsPlugin *) object;

	switch (prop_id)
	{
		case PROP_WINDOW:
			plugin->window = GEDIT_WINDOW (g_value_get_object (value));
			break;
		default:
			G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
			break;
	}
}

static void
gedit_modifications_plugin_get_property (GObject    *object,
                                    guint       prop_id,
                                    GValue     *value,
                                    GParamSpec *pspec)
{
	GeditModificationsPlugin *plugin = (GeditModificationsPlugin *) object;

	switch (prop_id)
	{
		case PROP_WINDOW:
			g_value_set_object (value, plugin->window);
			break;
		default:
			G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
			break;
	}
}

static void
gedit_modifications_plugin_init (GeditModificationsPlugin *plugin)
{
	plugin->window = NULL;
}

static void
gedit_modifications_plugin_class_init (GeditModificationsPluginClass *klass)
{
	GObjectClass *object_class = G_OBJECT_CLASS (klass);

	object_class->set_property = gedit_modifications_plugin_set_property;
	object_class->get_property = gedit_modifications_plugin_get_property;

	/* GeditWindowActivatable defines a "window" construct property; override it so we
	 * receive the GeditWindow. */
	g_object_class_override_property (object_class, PROP_WINDOW, "window");
}

/* Required by G_DEFINE_DYNAMIC_TYPE_EXTENDED (dynamic types get a class_finalize). Nothing
 * to tear down at the class level. */
static void
gedit_modifications_plugin_class_finalize (GeditModificationsPluginClass *klass)
{
	(void) klass;
}

/*
 * libpeas C-plugin entry point. libpeas calls peas_register_types() on load; we register
 * our GType and tell libpeas it implements GeditWindowActivatable. This is the standard
 * boilerplate every shipped C gedit plugin uses.
 */
G_MODULE_EXPORT void
peas_register_types (PeasObjectModule *module)
{
	gedit_modifications_plugin_register_type (G_TYPE_MODULE (module));

	peas_object_module_register_extension_type (
		module,
		GEDIT_TYPE_WINDOW_ACTIVATABLE,
		GEDIT_TYPE_MODIFICATIONS_PLUGIN);
}
