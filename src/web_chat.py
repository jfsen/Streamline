from gi.repository import Adw, Gtk, WebKit, GLib

class ChatPage(Adw.NavigationPage):
    def __init__(self, streamer):
        super().__init__(
            title=f"{streamer}'s Chat"
        )

        # Create toolbar with back button
        self.header = Adw.HeaderBar()
        self.header.add_css_class("flat")
        
        # Create main content box
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.append(self.header)
        
        # Create WebView with custom settings
        settings = WebKit.Settings()
        settings.set_property("enable-javascript", True)
        settings.set_property("enable-smooth-scrolling", True)
        settings.set_property("hardware-acceleration-policy", WebKit.HardwareAccelerationPolicy.ALWAYS)
        
        self.webview = WebKit.WebView()
        self.webview.set_settings(settings)

        # Create loading spinner
        self.spinner = Gtk.Spinner()
        self.spinner.start()
        
        # Create status page for loading
        self.status_page = Adw.StatusPage(
            title="Loading Chat",
            child=self.spinner
        )
        
        # Create box to hold content
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.append(self.status_page)
        
        # Load the chat
        chat_url = f"https://www.twitch.tv/embed/{streamer}/chat?darkpopout&parent=streamline"
        self.webview.load_uri(chat_url)
        
        # Connect load signals
        self.webview.connect('load-changed', self._on_load_changed)
        
        # Create scrolled window
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_child(self.webview)
        
        # Add scrolled window to content box
        self.content_box.append(self.box)
        
        # Set the content box as the page content
        self.set_child(self.content_box)
    
    def _on_load_changed(self, web_view, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            # Hide status page and show webview
            self.box.remove(self.status_page)
            self.box.append(self.scrolled)
            
            # Inject custom CSS and handle cookie consent
            js_code = """
                // Set dark mode
                document.documentElement.style.setProperty('color-scheme', 'dark');
                document.body.style.backgroundColor = 'transparent';
                
                // Handle cookie consent
                function handleCookieConsent() {
                    // Find and click the reject button if present
                    const rejectButton = document.querySelector('[data-a-target="consent-banner-reject"]');
                    if (rejectButton) {
                        rejectButton.click();
                    }
                    
                    // Hide the consent banner directly if still present
                    const consentBanner = document.querySelector('.consent-banner');
                    if (consentBanner) {
                        consentBanner.style.display = 'none';
                    }
                }
                
                // Run immediately and also after a short delay to ensure DOM is loaded
                handleCookieConsent();
                setTimeout(handleCookieConsent, 1000);
                
                // Create observer to handle banner if it appears later
                const observer = new MutationObserver((mutations) => {
                    handleCookieConsent();
                });
                
                observer.observe(document.body, {
                    childList: true,
                    subtree: true
                });
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None)