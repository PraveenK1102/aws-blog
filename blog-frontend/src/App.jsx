import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import { getToken, removeToken } from "./api";
import Home from "./pages/Home";
import PostsList from "./pages/PostsList";
import PostDetail from "./pages/PostDetail";
import NewPost from "./pages/NewPost";
import EditPost from "./pages/EditPost";
import Login from "./pages/Login";
import Register from "./pages/Register";

function Nav() {
  const token = getToken();
  return (
    <nav>
      <Link to="/">Home</Link>
      <Link to="/posts">Posts</Link>
      {token ? (
        <>
          <Link to="/posts/new">New post</Link>
          <a href="/" onClick={() => { removeToken(); window.location.href = "/"; }}>Logout</a>
        </>
      ) : (
        <>
          <Link to="/login">Login</Link>
          <Link to="/register">Register</Link>
        </>
      )}
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Nav />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/posts" element={<PostsList />} />
        <Route path="/posts/new" element={<NewPost />} />
        <Route path="/posts/:id" element={<PostDetail />} />
        <Route path="/posts/:id/edit" element={<EditPost />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
      </Routes>
    </BrowserRouter>
  );
}
