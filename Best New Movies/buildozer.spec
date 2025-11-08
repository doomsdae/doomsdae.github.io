[app]
title = New Movies
package.name = newmovies
package.domain = org.moviefinder
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,env
version = 0.1
requirements = python3,kivy,kivymd,requests,python-dotenv,charset_normalizer,idna,urllib3,certifi,python-dateutil,six
orientation = portrait
osx.python_version = 3
osx.kivy_version = 2.2.1
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 21
android.sdk = 33
android.ndk = 25b
android.arch = arm64-v8a
p4a.branch = master
android.enable_androidx = True

[buildozer]
log_level = 2
warn_on_root = 1