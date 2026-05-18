const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const bodyParser = require('body-parser');
const app = express();
const db = new sqlite3.Database(':memory:');

db.serialize(() => {
  db.run("CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT, phone TEXT)");
});

app.use(bodyParser.json());
app.use(express.static('public'));

app.get('/api/records', (req, res) => {
  db.all("SELECT * FROM records", [], (err, rows) => {
    res.json(rows);
  });
});

app.post('/api/records', (req, res) => {
  const { name, phone } = req.body;
  db.run("INSERT INTO records (name, phone) VALUES (?, ?)", [name, phone], function() {
    res.json({ id: this.lastID, name, phone });
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Сервер запущен на порту ${PORT}`));
