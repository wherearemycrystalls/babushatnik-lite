from aiogram.filters.callback_data import CallbackData


class ApartmentAction(CallbackData, prefix="apt"):
    action: str
    ad_id: int


class FavouritesAction(CallbackData, prefix="favs"):
    action: str
    archived: bool = False
    page: int = 0


class ProfileAction(CallbackData, prefix="profile"):
    action: str
    profile_id: int = 0
    page: int = 0


class SearchAction(CallbackData, prefix="search"):
    action: str
    profile_id: int = 0
    page: int = 0
