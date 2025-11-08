from kivy.lang import Builder
from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.list import MDList, TwoLineListItem
from kivymd.uix.card import MDCard
from kivy.uix.scrollview import ScrollView
from kivymd.uix.button import MDRaisedButton
from kivymd.uix.label import MDLabel
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.clock import Clock
from functools import partial
import webbrowser
import threading
import os

# Import your existing movie finder logic
from movie_finder import (
    load_dotenv, fetch_provider_id_map, fetch_genre_map,
    discover_new_movies, get_watch_providers, get_imdb_id,
    pick_flatrate_providers, rank_score
)

# GUI Layout
KV = '''
MDScreen:
    MDBoxLayout:
        orientation: 'vertical'
        
        MDTopAppBar:
            title: "New Movies"
            right_action_items: [["theme-light-dark", lambda x: app.toggle_theme()]]
            elevation: 2

        MDBoxLayout:
            orientation: 'vertical'
            padding: "16dp"
            spacing: "8dp"
            
            MDCard:
                orientation: 'vertical'
                padding: "8dp"
                size_hint_y: None
                height: "120dp"
                
                MDLabel:
                    text: "Filter by Genre"
                    theme_text_color: "Secondary"
                    size_hint_y: None
                    height: "24dp"
                
                ScrollView:
                    size_hint_y: None
                    height: "80dp"
                    
                    MDBoxLayout:
                        id: genre_chips
                        orientation: 'horizontal'
                        size_hint_x: None
                        spacing: "8dp"
                        padding: "4dp"
                        width: self.minimum_width
            
            MDLabel:
                id: status_label
                text: "Loading movies..."
                theme_text_color: "Secondary"
                size_hint_y: None
                height: "24dp"
            
            ScrollView:
                MDList:
                    id: movies_list
'''

class MovieFinderApp(MDApp):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_cls.primary_palette = "Blue"
        self.theme_cls.theme_style = "Dark"
        self.movies_by_genre = {}
        self.current_genre = "All"
        
    def build(self):
        return Builder.load_string(KV)

    def on_start(self):
        # Start loading movies in background
        threading.Thread(target=self.load_movies, daemon=True).start()

    def load_movies(self):
        try:
            # Initialize environment
            load_dotenv()
            
            # Set up TMDb API
            if not os.getenv("TMDB_API_KEY"):
                Clock.schedule_once(lambda dt: self.show_error("Missing TMDB_API_KEY in .env"))
                return
                
            # Fetch movies
            id_map, id_to_name = fetch_provider_id_map()
            allowed_ids = set(id_to_name.keys())
            genre_map = fetch_genre_map()
            candidates = discover_new_movies(pages=5)
            
            # Process movies
            movies = []
            for m in candidates:
                pid = m["id"]
                wp = get_watch_providers(pid)
                providers = pick_flatrate_providers(wp, allowed_ids)
                if not providers:
                    continue

                imdb_id = get_imdb_id(pid)
                genre_ids = m.get("genre_ids") or []
                genres = [genre_map[g] for g in genre_ids if g in genre_map] or ["Uncategorized"]
                
                movie_data = {
                    "title": m.get("title") or "",
                    "year": (m.get("release_date") or "")[:4],
                    "rating": round(m.get("vote_average", 0.0), 1),
                    "votes": int(m.get("vote_count", 0) or 0),
                    "popularity": float(m.get("popularity", 0.0) or 0.0),
                    "providers": ", ".join(providers),
                    "imdb_id": imdb_id,
                    "genres": genres
                }
                movies.append(movie_data)
            
            # Group by genre
            self.movies_by_genre = {"All": movies}
            for movie in movies:
                for genre in movie["genres"]:
                    if genre not in self.movies_by_genre:
                        self.movies_by_genre[genre] = []
                    self.movies_by_genre[genre].append(movie)
            
            # Sort movies in each genre
            for genre in self.movies_by_genre:
                self.movies_by_genre[genre].sort(
                    key=lambda x: rank_score(x["rating"], x["votes"], x["popularity"]),
                    reverse=True
                )
            
            # Update UI
            Clock.schedule_once(self.update_ui)
            
        except Exception as e:
            Clock.schedule_once(lambda dt: self.show_error(str(e)))

    def update_ui(self, *args):
        # Clear existing items
        self.root.ids.genre_chips.clear_widgets()
        self.root.ids.movies_list.clear_widgets()
        
        # Add genre chips
        for genre in ["All"] + sorted(self.movies_by_genre.keys()):
            if genre == "All":
                continue  # Skip "All" for now as it will be added first
            btn = MDRaisedButton(
                text=genre,
                on_press=partial(self.filter_genre, genre),
                size_hint=(None, None),
                height="36dp"
            )
            self.root.ids.genre_chips.add_widget(btn)
        
        # Show initial movies (All genres)
        self.filter_genre("All")
        
        # Update status
        total = len(self.movies_by_genre.get("All", []))
        self.root.ids.status_label.text = f"Found {total} movies"

    def filter_genre(self, genre, *args):
        self.current_genre = genre
        self.root.ids.movies_list.clear_widgets()
        
        movies = self.movies_by_genre.get(genre, [])
        for movie in movies:
            item = TwoLineListItem(
                text=f"{movie['title']} ({movie['year']})",
                secondary_text=f"⭐ {movie['rating']} • {movie['providers']}",
                on_release=partial(self.open_movie, movie)
            )
            self.root.ids.movies_list.add_widget(item)

    def open_movie(self, movie, *args):
        if movie.get("imdb_id"):
            url = f"https://www.imdb.com/title/{movie['imdb_id']}/"
            webbrowser.open(url)

    def toggle_theme(self):
        self.theme_cls.theme_style = (
            "Light" if self.theme_cls.theme_style == "Dark" else "Dark"
        )

    def show_error(self, message):
        self.root.ids.status_label.text = f"Error: {message}"

if __name__ == "__main__":
    MovieFinderApp().run()