# Google вход для SpendIt

В SpendIt уже реализованы вход и регистрация через Google OpenID Connect.

1. Создайте OAuth 2.0 Client ID в Google Cloud Console.
2. Тип приложения: Web application.
3. Добавьте Authorized redirect URI:

`http://127.0.0.1:5000/auth/google/callback`

Для запуска с другим доменом добавьте callback URL вашего домена.

4. Скопируйте данные в `.env`:

```env
SECRET_KEY=replace-with-a-long-random-secret
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

Первый вход через Google автоматически создаёт аккаунт. Если такой email уже есть в SpendIt, Google-профиль привязывается к существующему аккаунту.

После первого входа новый пользователь проходит анкету рекомендаций. Для существующего аккаунта обычный парольный вход продолжает работать.
