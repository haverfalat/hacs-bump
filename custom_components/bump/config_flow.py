"""Import an existing login or explicitly request a phone/code login."""
from functools import partial
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from .const import DOMAIN, DEFAULT_STALE_SECONDS
from .vendor.bump_read import Client
from .vendor.bump_session import PhoneLogin, import_session

SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class BumpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.login = PhoneLogin(Client())

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id='user', menu_options=['session', 'phone'])

    async def async_step_reauth(self, entry_data):
        return await self.async_step_user()

    async def async_step_reconfigure(self, user_input=None):
        return await self.async_step_user()

    async def finish(self, exported):
        session = import_session(exported)
        await self.hass.async_add_executor_job(Client().friend_count, session['token'])
        uid = session['user_uid']
        if self.context.get('source') in ('reauth', 'reconfigure'):
            entry = self.hass.config_entries.async_get_entry(self.context['entry_id'])
            if entry.unique_id != uid:
                raise ValueError('Account mismatch')
            self.hass.config_entries.async_update_entry(entry, data={'session': exported})
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason='reauth_successful')
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title='Bump Friends', data={'session': exported})

    async def async_step_session(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                return await self.finish(user_input['session'].strip())
            except (ValueError, TypeError):
                errors['base'] = 'invalid_session'
            except (OSError, RuntimeError) as error:
                errors['base'] = 'invalid_auth' if getattr(error, 'status', None) == 16 else 'cannot_connect'
        return self.async_show_form(step_id='session', data_schema=vol.Schema({vol.Required('session'): SECRET}), errors=errors)

    async def async_step_phone(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(partial(self.login.send_code, **user_input))
                return await self.async_step_code()
            except ValueError:
                errors['base'] = 'invalid_phone'
            except (OSError, RuntimeError):
                errors['base'] = 'cannot_connect'
        return self.async_show_form(step_id='phone', data_schema=vol.Schema({
            vol.Required('phone'): str, vol.Required('calling_code'): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
            vol.Optional('first_name', default=''): str, vol.Optional('last_name', default=''): str,
            vol.Optional('age', default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=150))}), errors=errors)

    async def async_step_code(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                exported = await self.hass.async_add_executor_job(self.login.verify_code, user_input['code'])
                return await self.finish(exported)
            except (ValueError, OSError, RuntimeError):
                errors['base'] = 'invalid_code'
        return self.async_show_form(step_id='code', data_schema=vol.Schema({vol.Required('code'): SECRET}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BumpOptionsFlow()


class BumpOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)
        return self.async_show_form(step_id='init', data_schema=vol.Schema({
            vol.Required('stale_seconds', default=self.config_entry.options.get('stale_seconds', DEFAULT_STALE_SECONDS)):
                vol.All(vol.Coerce(int), vol.Range(min=30, max=86400))}))
